"""
Análise de log — agrupar, contar e casar com o que já se sabe.

Sem modelo de linguagem, e não por economia: para um domínio estreito e
com ação destrutiva (reiniciar container de reconhecimento facial em
produção), regra escrita por quem operou a máquina vale mais que fluência.
Ver `catalogo_erros.py`.

O que este serviço faz, em três passos:

1. **Normaliza** a linha — troca timestamp, UUID, IP, hash e número por
   marcador. `Camera 17 timeout after 5031ms` e `Camera 42 timeout after
   87ms` viram o mesmo molde.
2. **Agrupa** pelo molde (fingerprint sha1). Mil ocorrências do mesmo erro
   viram uma linha com contador — é o "auto filtro".
3. **Casa** com o catálogo de erros conhecidos e, quando bate, entrega
   junto a causa provável e a ação.

Sobre custo: a coleta é limitada de propósito. Só lê log de serviço que
**já está com incidente aberto**, no máximo `analise.servicos_por_ciclo`
serviços por vez, e no máximo uma vez a cada `analise.intervalo_min`
minutos por serviço. Ler continuamente os quatro servidores seria
exatamente o tipo de peso que este painel evita — o appserver gera ~8 GB
de log por dia.
"""
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.models.log_padrao import LogPadrao
from app.services.catalogo_erros import POR_CHAVE, classificar

log = logging.getLogger("faceops.analise")

# Ordem importa: o mais específico primeiro, senão `<n>` come tudo.
SUBSTITUICOES = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<ts>"),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<hora>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<hash>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<hex>"),
    (re.compile(r"/[\w./-]*\d[\w./-]*"), "<caminho>"),
    (re.compile(r"\b\d+(?:[.,]\d+)?(?:ms|s|kb|mb|gb|%)\b", re.I), "<medida>"),
    (re.compile(r"\b\d+\b"), "<n>"),
]

RE_ESPACO = re.compile(r"\s+")


def normalizar(linha: str) -> str:
    """A linha sem o que muda entre duas ocorrências do mesmo erro."""
    texto = linha.strip()
    for regex, marcador in SUBSTITUICOES:
        texto = regex.sub(marcador, texto)
    return RE_ESPACO.sub(" ", texto).strip()[:400]


def impressao(molde: str) -> str:
    return hashlib.sha1(molde.encode("utf-8", "replace")).hexdigest()[:32]


def analisar_texto(texto: str, maximo_linhas: int = 500) -> list[dict]:
    """
    Texto de log -> lista de moldes, do mais frequente para o menos.

    Função pura: não toca banco nem rede, e é onde os testes batem.
    """
    achados: dict[str, dict] = {}
    linhas = texto.splitlines()[-maximo_linhas:] if texto else []

    for linha in linhas:
        linha = linha.strip()
        if not linha:
            continue
        nivel, conhecido = classificar(linha)
        # `info` não entra: guardar tudo seria virar um segundo syslog.
        if nivel == "info":
            continue

        molde = normalizar(linha)
        if not molde:
            continue
        chave = impressao(molde)

        item = achados.get(chave)
        if item is None:
            achados[chave] = {
                "fingerprint": chave,
                "molde": molde,
                "exemplo": linha[:600],
                "nivel": nivel,
                "padrao_conhecido": conhecido["chave"] if conhecido else "",
                "ocorrencias": 1,
            }
        else:
            item["ocorrencias"] += 1
            # Erro manda sobre aviso quando o mesmo molde aparece nos dois.
            if nivel == "erro":
                item["nivel"] = "erro"

    return sorted(achados.values(), key=lambda a: a["ocorrencias"], reverse=True)


class LogAnaliseService:
    def __init__(self, stack, config=None) -> None:
        self.stack = stack
        self.config = config
        # (host_id, servico) -> quando foi lido pela última vez. Só memória:
        # relê depois de um restart do painel, e reler não custa nada além
        # de uma leitura a mais.
        self._ultima_leitura: dict[tuple[int, str], datetime] = {}

    def _cfg(self, chave: str, padrao):
        if self.config is None:
            return padrao
        try:
            return self.config.get(chave)
        except (KeyError, ValueError, TypeError):
            return padrao

    # ── Gravação ───────────────────────────────────────────────────────

    async def registrar(self, db, host_id: int, servico: str, achados: list[dict]) -> int:
        """Soma os achados aos moldes já conhecidos daquele host/serviço."""
        agora = datetime.now(timezone.utc)
        novos = 0

        for a in achados:
            r = await db.execute(
                select(LogPadrao).where(
                    LogPadrao.host_id == host_id,
                    LogPadrao.servico == servico,
                    LogPadrao.fingerprint == a["fingerprint"],
                )
            )
            existente = r.scalars().first()
            if existente is None:
                db.add(LogPadrao(
                    host_id=host_id, servico=servico, fingerprint=a["fingerprint"],
                    nivel=a["nivel"], padrao_conhecido=a["padrao_conhecido"],
                    molde=a["molde"], exemplo=a["exemplo"],
                    ocorrencias=a["ocorrencias"], primeira_vez=agora, ultima_vez=agora,
                ))
                novos += 1
            else:
                existente.ocorrencias += a["ocorrencias"]
                existente.ultima_vez = agora
                existente.exemplo = a["exemplo"]
                if a["nivel"] == "erro":
                    existente.nivel = "erro"
        return novos

    # ── Coleta, limitada ───────────────────────────────────────────────

    def _pode_ler(self, host_id: int, servico: str, agora: datetime) -> bool:
        intervalo = int(self._cfg("analise.intervalo_min", 5))
        anterior = self._ultima_leitura.get((host_id, servico))
        if anterior and agora - anterior < timedelta(minutes=intervalo):
            return False
        return True

    async def analisar_servicos(
        self,
        db,
        host,
        servicos: list[str],
        containers: dict | None = None,
        agora: datetime | None = None,
        forcar: bool = False,
    ) -> int:
        """
        Lê e analisa o log dos serviços indicados. Chamado pelo ciclo do
        monitor **apenas** com serviços que já têm incidente aberto.

        `forcar=True` é o clique da tela: ignora o intervalo e o
        liga/desliga automático, porque ali a leitura foi pedida por uma
        pessoa e não pelo laço de fundo.
        """
        if not forcar and not bool(self._cfg("analise.ativa", True)):
            return 0

        agora = agora or datetime.now(timezone.utc)
        teto = len(servicos) if forcar else int(self._cfg("analise.servicos_por_ciclo", 3))
        linhas = int(self._cfg("analise.linhas", 200))

        # `docker logs` quer o nome do CONTAINER, e o incidente guarda o
        # nome do SERVIÇO do compose — sem o mapa, a leitura falharia em
        # silêncio. Quando não veio pronto (clique da tela), resolvemos
        # uma vez só, aqui.
        if containers is None:
            try:
                dados = await self.stack.list_services(host)
                containers = {s["servico"]: s["nome"] for s in dados.get("servicos", [])}
            except Exception as exc:
                log.info("não consegui mapear containers de %s: %s", host.name, exc)
                containers = {}

        analisados = 0
        for servico in servicos:
            if analisados >= teto:
                break
            if not forcar and not self._pode_ler(host.id, servico, agora):
                continue
            alvo = containers.get(servico, servico)
            try:
                texto = await self.stack.logs(host, alvo, linhas=linhas)
            except Exception as exc:
                # Log ilegível não pode derrubar o ciclo do monitor: o
                # container pode ter sumido entre a leitura e agora.
                log.info("análise de log de %s em %s: %s", servico, host.name, exc)
                self._ultima_leitura[(host.id, servico)] = agora
                continue

            achados = analisar_texto(texto)
            if achados:
                await self.registrar(db, host.id, servico, achados)
            self._ultima_leitura[(host.id, servico)] = agora
            analisados += 1

        return analisados

    # ── Consulta ───────────────────────────────────────────────────────

    async def listar(self, db, host_id: int | None = None, dias: int = 7, limite: int = 50) -> list[dict]:
        desde = datetime.now(timezone.utc) - timedelta(days=max(1, dias))
        condicoes = [LogPadrao.ultima_vez >= desde]
        if host_id is not None:
            condicoes.append(LogPadrao.host_id == host_id)

        r = await db.execute(
            select(LogPadrao)
            .where(*condicoes)
            .order_by(LogPadrao.ocorrencias.desc())
            .limit(limite)
        )

        saida = []
        for p in r.scalars().all():
            conhecido = POR_CHAVE.get(p.padrao_conhecido)
            saida.append({
                "id": p.id,
                "host_id": p.host_id,
                "servico": p.servico,
                "nivel": p.nivel,
                "molde": p.molde,
                "exemplo": p.exemplo,
                "ocorrencias": p.ocorrencias,
                "primeira_vez": p.primeira_vez.isoformat(),
                "ultima_vez": p.ultima_vez.isoformat(),
                # Quando o catálogo reconhece, o achado deixa de ser "erro
                # estranho no log" e vira causa + ação + tela para clicar.
                "conhecido": None if not conhecido else {
                    "chave": conhecido["chave"],
                    "titulo": conhecido["titulo"],
                    "causa": conhecido["causa"],
                    "acao": conhecido["acao"],
                    "onde": conhecido["onde"],
                    "fonte": conhecido.get("fonte", ""),
                },
            })
        return saida

    # ── Faxina ─────────────────────────────────────────────────────────

    @staticmethod
    async def limpar(db, dias: int) -> int:
        if dias <= 0:
            return 0
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(delete(LogPadrao).where(LogPadrao.ultima_vez < corte))
        return r.rowcount or 0
