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
