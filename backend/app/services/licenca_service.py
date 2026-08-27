"""
Licenciamento do FindFace lido **de dentro do servidor**, por SSH.

A licença não vem da API do ffsecurity — vem do NTLS, um serviço à parte.
E o NTLS atende em `localhost` sem exigir login: quem já está dentro da
máquina não precisa provar de novo quem é. É por isso que este caminho
existe ao lado do HTTP autenticado: o painel **já tem** SSH com sudo em
todos os servidores; depender também de uma senha de aplicação para ler
licença é uma credencial a mais para alguém errar, e foi exatamente o que
aconteceu no ambiente (senha de API recusada com `Invalid username/
password`, enquanto o SSH funcionava o tempo todo).

Endereços observados no bundle da interface do FindFace desta instalação:

    Paths = {License: "/ntls/v1/licenses.json",
             UsageReport: "/ntls/v1/usage-report.json"}

Servidos pelo `findface-ntls`, que escuta na 3185 e é publicado pelo nginx
em `/ntls/`. O script tenta os dois — porta direta e caminho do nginx —
porque instalação com nginx próprio pode não expor um deles.
"""
import json
import logging

from app.services.ssh_service import SSHError, SSHService

log = logging.getLogger("faceops.licenca")

SEP = "###FACEOPS:"

# Ordem de tentativa. Porta direta primeiro: é o serviço em si, sem
# depender da configuração do nginx.
URLS_LICENCA = (
    "http://127.0.0.1:3185/v1/licenses.json",
    "http://127.0.0.1/ntls/v1/licenses.json",
    "http://localhost/ntls/v1/licenses.json",
)
URLS_USO = (
    "http://127.0.0.1:3185/v1/usage-report.json",
    "http://127.0.0.1/ntls/v1/usage-report.json",
)

# `curl` cobre a maioria; `python3` existe em qualquer Ubuntu de servidor e
# cobre a máquina enxuta. Sem os dois, o script diz isso em vez de calar.
SCRIPT = r"""
set +e
buscar() {
  for u in "$@"; do
    if command -v curl >/dev/null 2>&1; then
      s="$(curl -fsS --max-time 8 "$u" 2>/dev/null)"
      if [ -n "$s" ]; then printf '%s' "$s"; return 0; fi
    fi
    if command -v python3 >/dev/null 2>&1; then
      s="$(python3 -c "import sys,urllib.request; sys.stdout.write(urllib.request.urlopen('$u', timeout=8).read().decode())" 2>/dev/null)"
      if [ -n "$s" ]; then printf '%s' "$s"; return 0; fi
    fi
  done
  return 1
}
echo "###FACEOPS:LICENCA"
buscar __URLS_LICENCA__
echo ""
echo "###FACEOPS:USO"
buscar __URLS_USO__
echo ""
echo "###FACEOPS:NTLS"
(ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | grep -E '3185|:80 ' | head -5
echo "###FACEOPS:FERRAMENTAS"
command -v curl >/dev/null 2>&1 && echo curl
command -v python3 >/dev/null 2>&1 && echo python3
echo "###FACEOPS:FIM"
"""


class LicencaError(Exception):
    pass


async def guardar_amostra(db, host_id: int, itens: list[dict]) -> int:
    """
    Grava o consumo de hoje, uma linha por recurso.

    O cenário real desta instalação: 453 dispositivos, todos detector
    externo, zero câmera licenciada em uso, e o recurso que consome é o
    `Objects TNT API`. Logo, a pergunta de capacidade não é "cabem quantas
    câmeras" — é **"em que ritmo consumimos objetos e quando acaba"**. A
    licença responde o instante; o ritmo só sai do histórico.

    Uma amostra por dia e por recurso: se já existe a de hoje, ela é
    atualizada em vez de duplicada. Recurso sem uso medido não vira linha —
    guardar zero seria inventar um consumo que ninguém observou.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models.licenca_amostra import LicencaAmostra

    if not itens:
        return 0

    hoje = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    gravadas = 0

    for item in itens:
        chave = item.get("chave") or item.get("recurso")
        usado = item.get("usado")
        limite = item.get("limite")
        if not chave or usado is None:
            continue

        existente = (
            await db.execute(
                select(LicencaAmostra).where(
                    LicencaAmostra.host_id == host_id,
                    LicencaAmostra.recurso == str(chave)[:64],
                    LicencaAmostra.ts >= hoje,
                    LicencaAmostra.ts < hoje + timedelta(days=1),
                )
            )
        ).scalars().first()

        if existente is not None:
            existente.usado = int(usado)
            existente.limite = int(limite or 0)
        else:
            db.add(
                LicencaAmostra(
                    host_id=host_id,
                    recurso=str(chave)[:64],
                    usado=int(usado),
                    limite=int(limite or 0),
                )
            )
        gravadas += 1

    await db.commit()
    return gravadas


async def serie(db, host_id: int, dias: int = 90) -> dict:
    """
    Consumo por recurso ao longo do tempo, com ritmo e projeção.

    O ritmo sai da diferença entre a primeira e a última amostra, dividida
    pelos dias entre elas — não da média das diferenças diárias. Motivo:
    limpeza de eventos faz o uso CAIR de um dia para o outro, e a média de
    variações diárias transformaria isso em ruído. Ponta a ponta responde a
    pergunta certa: "no ritmo dos últimos N dias, quando acaba?".

    Projeção só quando o consumo é crescente e há limite. Projetar queda
    daria data de esgotamento no passado, o que é pior que não projetar.
    """
    from sqlalchemy import select

    from app.models.licenca_amostra import LicencaAmostra

    dias = max(2, min(int(dias), 730))
    from datetime import datetime, timedelta, timezone

    desde = datetime.now(timezone.utc) - timedelta(days=dias)

    linhas = (
        await db.execute(
            select(LicencaAmostra)
            .where(LicencaAmostra.host_id == host_id, LicencaAmostra.ts >= desde)
            .order_by(LicencaAmostra.recurso, LicencaAmostra.ts)
        )
    ).scalars().all()

    por_recurso: dict[str, list] = {}
    for linha in linhas:
        por_recurso.setdefault(linha.recurso, []).append(linha)

    saida = []
    for recurso, amostras in por_recurso.items():
        pontos = [
            {"ts": a.ts.isoformat(), "usado": a.usado, "limite": a.limite}
            for a in amostras
        ]
        por_dia = None
        dias_para_o_fim = None
        if len(amostras) >= 2:
            primeiro, ultimo = amostras[0], amostras[-1]
            intervalo = (ultimo.ts - primeiro.ts).total_seconds() / 86400
            if intervalo >= 0.5:
                por_dia = round((ultimo.usado - primeiro.usado) / intervalo, 1)
                limite = ultimo.limite or 0
                if por_dia > 0 and limite > 0 and ultimo.usado < limite:
                    dias_para_o_fim = int((limite - ultimo.usado) / por_dia)

        saida.append({
            "recurso": recurso,
            "pontos": pontos,
            "amostras": len(pontos),
            "por_dia": por_dia,
            "dias_para_o_fim": dias_para_o_fim,
            "usado": amostras[-1].usado if amostras else None,
            "limite": amostras[-1].limite if amostras else None,
        })

    saida.sort(key=lambda r: (r["dias_para_o_fim"] is None, r["dias_para_o_fim"] or 0))
    return {"dias": dias, "recursos": saida}


def _secoes(saida: str) -> dict[str, str]:
    secoes: dict[str, str] = {}
    atual = None
    buf: list[str] = []
    for linha in saida.splitlines():
        if linha.startswith(SEP):
            if atual is not None:
                secoes[atual] = "\n".join(buf).strip()
            atual = linha[len(SEP):].strip()
            buf = []
        elif atual is not None:
            buf.append(linha)
    if atual is not None:
        secoes[atual] = "\n".join(buf).strip()
    return secoes


class LicencaService:
    """Leitura de licença pelo lado de dentro. Só leitura, nada muda."""

    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh

    async def ler(self, host) -> dict:
        script = SCRIPT.replace("__URLS_LICENCA__", " ".join(URLS_LICENCA)).replace(
            "__URLS_USO__", " ".join(URLS_USO)
        )

        try:
            resultado = await self.ssh.run(host, script, timeout=60)
        except SSHError as exc:
            raise LicencaError(f"não consegui rodar no servidor: {exc}") from exc

        secoes = _secoes(resultado.stdout or "")
        bruto_licenca = secoes.get("LICENCA", "").strip()
        bruto_uso = secoes.get("USO", "").strip()

        if not bruto_licenca:
            ferramentas = secoes.get("FERRAMENTAS", "").split()
            escutando = secoes.get("NTLS", "").strip()
            detalhe = []
            if not ferramentas:
                detalhe.append("o servidor não tem curl nem python3")
            if not escutando:
                detalhe.append("nada escutando na 3185 nem na 80")
            raise LicencaError(
                "o serviço de licença (NTLS) não respondeu dentro de "
                f"'{host.name}'. Tentado: {', '.join(URLS_LICENCA)}"
                + (". " + "; ".join(detalhe) if detalhe else "")
            )

        try:
            dados = json.loads(bruto_licenca)
        except json.JSONDecodeError as exc:
            raise LicencaError(
                f"o NTLS de '{host.name}' respondeu algo que não é JSON: "
                f"{bruto_licenca[:200]}"
            ) from exc

        uso = None
        if bruto_uso:
            try:
                uso = json.loads(bruto_uso)
            except json.JSONDecodeError:
                uso = None

        # O achatamento é o mesmo do caminho HTTP: um formato só, uma tela
        # só, e nada de duas verdades sobre a mesma licença.
        from app.services.ffapi_service import _cabecalho_licenca, _itens_licenca

        itens = _itens_licenca(dados)
        for item in itens:
            item["estourado"] = bool(
                item["limite"] is not None
                and item["usado"] is not None
                and not item["ilimitado"]
                and item["usado"] > item["limite"]
            )

        return {
            "via": "ssh",
            "url": f"{host.name} (localhost)",
            "caminho": URLS_LICENCA[0],
            "tentativas": [],
            "cabecalho": _cabecalho_licenca(dados),
            "estourados": sum(1 for i in itens if i.get("estourado")),
            "cameras_cadastradas": None,
            "itens": itens,
            "bruto": dados,
            "relatorio_uso": uso,
        }
