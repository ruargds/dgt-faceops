"""
Quem recebe o quê — e a mensagem que chega.

Regra de ouro deste serviço: **aviso que repete vira aviso que se
ignora**. Por isso cada evento é mandado uma vez só (`chave` de
deduplicação), há teto de envios por ciclo, e "voltou ao normal" é opcional
por regra.

Casamento de regra, do mais específico para o mais geral:

    (host, serviço)  >  (host, todos)  >  (todos, serviço)  >  (todos, todos)

A primeira que casar decide. `nivel_minimo` da regra vencedora define se o
evento passa: `critico` só deixa passar o que parou de fato; `atencao`
deixa passar tudo.
"""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.core.vault import decrypt_secret
from app.models.notificacao import NotificacaoConta, NotificacaoEnvio, NotificacaoRegra
from app.services import telegram_service

log = logging.getLogger("faceops.notificacao")

# Teto por ciclo. Se dez serviços caem juntos, a pessoa precisa saber que
# dez caíram — não receber dez mensagens no meio da madrugada.
TETO_POR_CICLO = 8

NIVEIS = {"atencao": 1, "critico": 2}

ICONE = {"critico": "🔴", "atencao": "🟡", "retorno": "🟢"}


def _quando(dt: datetime) -> str:
    return dt.astimezone().strftime("%d/%m %H:%M")


def _duracao(segundos: float | None) -> str:
    if not segundos or segundos < 0:
        return ""
    s = int(segundos)
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}min"
    h = m // 60
    if h < 24:
        return f"{h}h{m % 60:02d}"
    return f"{h // 24}d{h % 24}h"


def montar_mensagem(evento: dict) -> str:
    """
    Mensagem curta, legível no aviso do celular sem abrir o app.

    Quatro linhas no máximo: o que, onde, por que (quando se sabe) e desde
    quando. Mensagem longa no Telegram vira bloco cinza cortado — quem
    está de plantão precisa decidir "levanto ou não" pela prévia.
    """
    host = evento.get("host") or "servidor"
    servico = evento.get("servico") or ""
    alvo = servico or "máquina inteira"

    if evento["tipo"] == "retorno":
        linhas = [f"{ICONE['retorno']} NORMALIZADO · {host}", f"{alvo} voltou"]
        d = _duracao(evento.get("duracao_s"))
        if d:
            linhas.append(f"Ficou fora {d}")
        return "\n".join(linhas)

    icone = ICONE.get(evento["nivel"], ICONE["atencao"])
    rotulo = "PARADO" if evento["nivel"] == "critico" else "ATENÇÃO"
    linhas = [f"{icone} {rotulo} · {host}", evento.get("texto") or f"{alvo} com problema"]

    causa = (evento.get("causa_provavel") or "").strip()
    if causa:
        # Uma frase só: a causa provável completa é longa e está na tela.
        linhas.append(f"Provável: {causa.split('.')[0][:140]}")

    inicio = evento.get("inicio")
    if isinstance(inicio, datetime):
        linhas.append(f"Desde {_quando(inicio)}")
    return "\n".join(linhas)


class NotificacaoService:
    def __init__(self, config=None) -> None:
        self.config = config

    def _cfg(self, chave: str, padrao):
        if self.config is None:
            return padrao
        try:
            return self.config.get(chave)
        except (KeyError, ValueError, TypeError):
            return padrao

    # ── Conta ──────────────────────────────────────────────────────────

    @staticmethod
    async def conta(db) -> NotificacaoConta | None:
        r = await db.execute(select(NotificacaoConta).limit(1))
        return r.scalars().first()

    # ── Regras ─────────────────────────────────────────────────────────

    @staticmethod
    def _casar(regras: list[NotificacaoRegra], host_id: int, servico: str):
        """A regra mais específica que se aplica, ou None."""
        for hid, srv in (
            (host_id, servico),
            (host_id, ""),
            (None, servico),
            (None, ""),
        ):
            if srv is None:
                continue
            for regra in regras:
                if regra.host_id == hid and regra.servico == srv:
                    return regra
        return None

    async def permitido(self, db, evento: dict) -> bool:
        """A regra deixa este evento passar?"""
        r = await db.execute(select(NotificacaoRegra).where(NotificacaoRegra.ativo.is_(True)))
        regras = list(r.scalars().all())
        return self.decidir(regras, evento)

    @classmethod
    def decidir(cls, regras: list[NotificacaoRegra], evento: dict) -> bool:
        regra = cls._casar(regras, evento.get("host_id"), evento.get("servico") or "")
        if regra is None:
            # Sem regra que cubra, não manda. Silêncio por omissão é o
            # padrão seguro: ninguém é surpreendido por aviso que não pediu.
            return False
        if evento["tipo"] == "retorno":
            return bool(regra.avisar_retorno)
        return NIVEIS.get(evento.get("nivel"), 1) >= NIVEIS.get(regra.nivel_minimo, 2)

    # ── Envio ──────────────────────────────────────────────────────────

    async def despachar(self, db, eventos: list[dict]) -> int:
        """
        Filtra pelas regras, evita repetido e manda. Devolve quantos foram.

        Nunca levanta: falha de notificação não pode derrubar o ciclo do
        monitor, que é quem grava a amostra e o incidente.
        """
        if not eventos:
            return 0
        try:
            conta = await self.conta(db)
            if conta is None or not conta.ativo or not conta.bot_token_enc or not conta.chat_id:
                return 0

            r = await db.execute(select(NotificacaoRegra).where(NotificacaoRegra.ativo.is_(True)))
            regras = list(r.scalars().all())
            if not regras:
                return 0

            token = decrypt_secret(conta.bot_token_enc)
        except Exception:
            log.exception("notificação: configuração ilegível")
            return 0

        enviados = 0
        for evento in eventos:
            if enviados >= TETO_POR_CICLO:
                log.warning("teto de %d notificações no ciclo atingido", TETO_POR_CICLO)
                break
            if not self.decidir(regras, evento):
                continue

            chave = evento.get("chave") or ""
            if chave and await self._ja_enviado(db, chave):
                continue

            texto = montar_mensagem(evento)
            registro = NotificacaoEnvio(chave=chave, texto=texto[:1000])
            try:
                await telegram_service.enviar(token, conta.chat_id, texto)
                registro.status = "enviado"
                enviados += 1
            except Exception as exc:
                registro.status = "falha"
                registro.erro = str(exc)[:300]
                log.warning("falha ao notificar (%s): %s", chave, registro.erro)
            db.add(registro)

        return enviados

    @staticmethod
    async def _ja_enviado(db, chave: str) -> bool:
        r = await db.execute(
            select(NotificacaoEnvio.id).where(
                NotificacaoEnvio.chave == chave, NotificacaoEnvio.status == "enviado"
            ).limit(1)
        )
        return r.scalar() is not None

    async def enviar_teste(self, db, usuario: str) -> dict:
        """Botão 'enviar teste' — prova que token e chat_id estão certos."""
        conta = await self.conta(db)
        if conta is None or not conta.bot_token_enc or not conta.chat_id:
            raise ValueError("configure o bot e o grupo antes de testar")

        token = decrypt_secret(conta.bot_token_enc)
        texto = (
            "✅ DGT FaceOps conectado\n"
            f"Teste enviado por {usuario}\n"
            "Este grupo vai receber os avisos configurados."
        )
        await telegram_service.enviar(token, conta.chat_id, texto)
        db.add(NotificacaoEnvio(chave=f"teste:{usuario}:{datetime.now(timezone.utc).timestamp()}",
                                texto=texto, status="enviado"))
        return {"ok": True}

    # ── Consulta e faxina ──────────────────────────────────────────────

    @staticmethod
    async def ultimos(db, limite: int = 30) -> list[dict]:
        r = await db.execute(
            select(NotificacaoEnvio).order_by(NotificacaoEnvio.ts.desc()).limit(limite)
        )
        return [
            {
                "id": e.id, "ts": e.ts.isoformat(), "texto": e.texto,
                "status": e.status, "erro": e.erro,
            }
            for e in r.scalars().all()
        ]

    @staticmethod
    async def limpar(db, dias: int) -> int:
        """
        Log de envio é operacional, não histórico. Passado o prazo, sai —
        inclusive as falhas: o que importa delas é o agora.
        """
        if dias <= 0:
            return 0
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(delete(NotificacaoEnvio).where(NotificacaoEnvio.ts < corte))
        return r.rowcount or 0
