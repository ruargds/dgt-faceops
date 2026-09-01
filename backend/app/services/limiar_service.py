"""
Limiares de alerta com exceção por host e/ou serviço.

O padrão de fábrica continua vivendo no catálogo de `config_service.py` —
um valor só, para a instalação inteira. Esta camada fica por cima dele e
resolve em cascata:

    override (host + serviço)  >  override (host, sem serviço)
        >  override (serviço, sem host)  >  padrão do catálogo

"Restaurar padrão" é só apagar a linha da exceção: sem override, a
resolução cai automaticamente de volta no catálogo — não existe um valor
"restaurado" para guardar, porque o padrão nunca deixou de existir.
"""
from sqlalchemy import delete, select

from app.models.limiar_override import LimiarOverride

# Chaves aceitas, e para qual nível cada uma se aplica. Nível de host:
# métrica da máquina (existe hoje em ConfigService, sob "alerta.<chave>").
# Nível de serviço: novidade — comportamento mínimo aceitável de um
# container específico.
CHAVES_HOST = frozenset({
    "disco_pct", "mem_pct", "swap_pct", "cpu_pct", "gpu_mem_pct", "gpu_temp",
})
CHAVES_SERVICO = frozenset({
    # Reinícios acumulados a partir dos quais um serviço "de pé" ainda
    # conta como problema — container em loop de restart não aparece como
    # "parado", mas está tão quebrado quanto.
    "servico_reinicios",
    # Minutos que um serviço pode ficar fora antes do alerta subir de
    # atenção para crítico.
    "servico_indisponivel_min",
})
CHAVES_VALIDAS = CHAVES_HOST | CHAVES_SERVICO


class LimiarService:
    def __init__(self, config=None) -> None:
        self.config = config

    def _padrao(self, chave: str, coringa):
        if self.config is None:
            return coringa
        try:
            return float(self.config.get(f"alerta.{chave}"))
        except (KeyError, ValueError, TypeError):
            return coringa

    async def resolver(self, db, chave: str, host_id: int | None, servico: str = "", padrao: float = 0.0) -> float:
        """Valor efetivo para (host, serviço, chave), já com a cascata aplicada."""
        servico = servico or ""
        candidatos: list[tuple[int | None, str]] = []
        if host_id is not None and servico:
            candidatos.append((host_id, servico))
        if host_id is not None:
            candidatos.append((host_id, ""))
        if servico:
            candidatos.append((None, servico))

        for hid, serv in candidatos:
            r = await db.execute(
                select(LimiarOverride.valor).where(
                    LimiarOverride.host_id == hid,
                    LimiarOverride.servico == serv,
                    LimiarOverride.chave == chave,
                )
            )
            valor = r.scalar()
            if valor is not None:
                return float(valor)

        return self._padrao(chave, padrao)

    async def resolver_lote(self, db, host_id: int | None) -> dict:
        """
        Todos os overrides que afetam um host — geral e por serviço —
        numa consulta só. Usado pelo ciclo do monitor, que roda uma vez
        por host a cada ciclo e não pode fazer N consultas por métrica.
        """
        r = await db.execute(
            select(LimiarOverride).where(
                (LimiarOverride.host_id == host_id) | (LimiarOverride.host_id.is_(None))
            )
        )
        overrides = list(r.scalars().all())

        saida: dict[str, float] = {}
        # Ordem: geral primeiro, host depois — host sobrescreve geral
        # quando os dois existem para a mesma chave/serviço.
        for o in sorted(overrides, key=lambda x: x.host_id is None, reverse=True):
            saida[f"{o.servico}::{o.chave}"] = float(o.valor)
        return saida

    async def listar(self, db) -> list[dict]:
        r = await db.execute(select(LimiarOverride).order_by(LimiarOverride.chave, LimiarOverride.servico))
        return [
            {
                "id": o.id,
                "host_id": o.host_id,
                "servico": o.servico,
                "chave": o.chave,
                "valor": o.valor,
                "atualizado_em": o.updated_at.isoformat(),
                "criado_por": o.created_by,
            }
            for o in r.scalars().all()
        ]

    async def salvar(
        self, db, chave: str, valor: float, host_id: int | None, servico: str, usuario: str
    ) -> dict:
        if chave not in CHAVES_VALIDAS:
            raise ValueError(f"chave de limiar desconhecida: {chave!r}")
        servico = servico or ""
        if chave in CHAVES_SERVICO and not servico:
            raise ValueError(f"'{chave}' é um limite por serviço — informe o serviço")
        if chave in CHAVES_HOST and servico:
            raise ValueError(f"'{chave}' é um limite de host — não se aplica a um serviço")

        r = await db.execute(
            select(LimiarOverride).where(
                LimiarOverride.host_id == host_id,
                LimiarOverride.servico == servico,
                LimiarOverride.chave == chave,
            )
        )
        existente = r.scalars().first()
        if existente:
            existente.valor = valor
            existente.created_by = usuario
        else:
            existente = LimiarOverride(
                host_id=host_id, servico=servico, chave=chave, valor=valor, created_by=usuario,
            )
            db.add(existente)
        await db.flush()
        return {
            "id": existente.id, "host_id": host_id, "servico": servico,
            "chave": chave, "valor": valor,
        }

    async def restaurar(self, db, override_id: int) -> bool:
        """Apaga a exceção — o valor volta a ser o padrão do catálogo."""
        r = await db.execute(delete(LimiarOverride).where(LimiarOverride.id == override_id))
        return bool(r.rowcount)
