"""
Estado dos componentes internos do FindFace, sem instalar nada.

**Por que não existe agente aqui.** A pergunta é legítima: para saber o que
acontece dentro dos containers, o caminho óbvio pareceria ser um agente em
cada máquina. Não é, por três razões:

1. **Os componentes já falam HTTP.** O manual da NtechLab documenta a porta
   de cada um (arquitetura, 2.4.1): `findface-extraction-api` na 18666,
   `findface-sf-api` na 18411, `findface-video-manager` na 18810/18811,
   `findface-video-worker` na 18999, `findface-ntls` na 3133/3185,
   `findface-upload` na 3333, e assim por diante. Todos atendem em
   `localhost` da máquina onde rodam.
2. **O painel já está dentro.** Ele tem SSH com sudo em todos os
   servidores — que é exatamente o alcance que um agente teria. Um agente
   não daria acesso novo: daria mais uma peça para instalar, atualizar e
   manter em quatro VMs, com porta aberta e credencial própria.
3. **Agente é superfície de ataque e dívida de operação.** O `SCOPE.md`
   assume "não instala agente" desde o começo, e não por preguiça: cada
   binário nosso rodando num servidor de reconhecimento facial é algo que
   precisa ser auditado, versionado e explicado numa auditoria.

Então: SSH abre a sessão, e de dentro dela o painel conversa com os
componentes pela porta que o fabricante documentou. Precisão de agente,
sem agente.

O que este serviço faz é **só leitura**: bate na porta, tenta os caminhos
de status conhecidos e conta o que respondeu. Nenhum comando muda estado.
"""
import json
import logging
import re

from app.services.ssh_service import SSHError, SSHService

log = logging.getLogger("faceops.internos")

SEP = "###FACEOPS:"

# Mapa oficial do manual (arquitetura do FindFace Multi 2.4.1). A porta é
# do fabricante; o caminho de status é descoberto em execução, porque o
# manual não fixa um para cada serviço — daí a lista de tentativas.
COMPONENTES = [
    {
        "nome": "findface-extraction-api",
        "porta": 18666,
        "papel": "Detecta objeto na imagem e extrai o vetor de características",
        "camada": "núcleo",
    },
    {
        "nome": "findface-sf-api",
        "porta": 18411,
        "papel": "API interna de detecção e reconhecimento",
        "camada": "núcleo",
    },
    {
        "nome": "findface-video-manager",
        "porta": 18810,
        "papel": "Distribui o trabalho de vídeo e configura os detectores",
        "camada": "vídeo",
    },
    {
        "nome": "findface-video-worker",
        "porta": 18999,
        "papel": "Reconhece objeto no vídeo e detecta vivacidade",
        "camada": "vídeo",
    },
    {
        "nome": "findface-upload",
        "porta": 3333,
        "papel": "Armazena as imagens originais e normalizadas",
        "camada": "mídia",
    },
    {
        "nome": "findface-ntls",
        "porta": 3185,
        "papel": "Servidor de licença",
        "camada": "licença",
    },
    {
        "nome": "findface-facerouter",
        "porta": 18820,
        "papel": "Define o que fazer com o objeto detectado",
        "camada": "núcleo",
    },
    {
        "nome": "findface-deduplicator",
        "porta": 18310,
        "papel": "Compara vetores para deduplicação",
        "camada": "núcleo",
    },
    {
        "nome": "findface-liveness-api",
        "porta": 18301,
        "papel": "Detecção de vivacidade como serviço",
        "camada": "núcleo",
    },
    {
        "nome": "findface-video-storage",
        "porta": 18611,
        "papel": "Gerencia os trechos de vídeo gravados",
        "camada": "gravação",
    },
    {
        "nome": "findface-video-streamer",
        "porta": 9000,
        "papel": "Entrega vídeo para visualização e download",
        "camada": "gravação",
    },
    {
        "nome": "findface-tarantool-server",
        "porta": 32001,
        "papel": "Banco dos vetores faciais",
        "camada": "dados",
    },
]

# Caminhos de status tentados em ordem. `/health` e `/status` cobrem os
# serviços em Go do núcleo; a raiz cobre o resto e serve como prova de vida.
CAMINHOS_STATUS = ("/health", "/status", "/")


class InternosError(Exception):
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


class InternosService:
    """Leitura do estado dos componentes, de dentro do servidor."""

    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh

    async def ler(self, host) -> dict:
        portas = " ".join(str(c["porta"]) for c in COMPONENTES)
        caminhos = " ".join(CAMINHOS_STATUS)

        # Uma execução só para todos os componentes: doze serviços vezes
        # três caminhos daria 36 handshakes SSH se fosse um por consulta.
        script = f"""
set +e
echo "{SEP}ESCUTANDO"
(ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | awk '{{print $4}}' | sed 's/.*://' | sort -un | tr '\\n' ' '
echo ""
echo "{SEP}RESPOSTAS"
for p in {portas}; do
  for c in {caminhos}; do
    if command -v curl >/dev/null 2>&1; then
      cod="$(curl -s -o /tmp/.faceops_body -w '%{{http_code}}' --max-time 4 "http://127.0.0.1:$p$c" 2>/dev/null)"
      if [ -n "$cod" ] && [ "$cod" != "000" ]; then
        corpo="$(head -c 400 /tmp/.faceops_body 2>/dev/null | tr -d '\\n' | tr -d '\\r')"
        echo "$p|$c|$cod|$corpo"
        break
      fi
    fi
  done
done
rm -f /tmp/.faceops_body 2>/dev/null
echo "{SEP}CONTAINERS"
docker ps --format '{{{{.Names}}}}|{{{{.Status}}}}' 2>/dev/null | grep -i -E 'findface|ffmulti|tarantool|ntls' | head -40
echo "{SEP}FIM"
"""

        try:
            r = await self.ssh.run(host, script, sudo=True, timeout=120)
        except SSHError as exc:
            raise InternosError(f"não consegui consultar '{host.name}': {exc}") from exc

        s = _secoes(r.stdout or "")

        escutando = {
            int(p) for p in re.findall(r"\d+", s.get("ESCUTANDO", "")) if p.isdigit()
        }

        respostas: dict[int, dict] = {}
        for linha in (s.get("RESPOSTAS") or "").splitlines():
            partes = linha.split("|", 3)
            if len(partes) < 3 or not partes[0].isdigit():
                continue
            porta = int(partes[0])
            corpo = partes[3] if len(partes) > 3 else ""
            resumo = ""
            # Corpo JSON pequeno vira resumo legível; o resto fica cru e
            # cortado. Melhor mostrar o que veio que interpretar demais.
            texto = corpo.strip()
            if texto.startswith("{"):
                try:
                    dados = json.loads(texto)
                    resumo = ", ".join(
                        f"{k}={v}"
                        for k, v in list(dados.items())[:4]
                        if not isinstance(v, (dict, list))
                    )
                except json.JSONDecodeError:
                    resumo = texto[:120]
            else:
                resumo = texto[:120]
            respostas[porta] = {
                "caminho": partes[1],
                "codigo": partes[2],
                "resumo": resumo,
            }

        containers: dict[str, str] = {}
        for linha in (s.get("CONTAINERS") or "").splitlines():
            if "|" in linha:
                nome, _, estado = linha.partition("|")
                containers[nome.strip()] = estado.strip()

        saida = []
        for comp in COMPONENTES:
            porta = comp["porta"]
            resposta = respostas.get(porta)
            container = next(
                (
                    f"{nome} ({estado})"
                    for nome, estado in containers.items()
                    if comp["nome"].split("findface-")[-1] in nome
                ),
                "",
            )
            codigo = resposta["codigo"] if resposta else ""
            # 2xx/3xx é vivo. 401/403 também é vivo — o serviço respondeu,
            # só não deixou entrar sem credencial, e isso é informação boa,
            # não falha.
            vivo = bool(codigo[:1] in ("2", "3") or codigo in ("401", "403"))
            saida.append({
                **comp,
                "escutando": porta in escutando,
                "codigo": codigo,
                "caminho": resposta["caminho"] if resposta else "",
                "resumo": resposta["resumo"] if resposta else "",
                "container": container,
                "vivo": vivo,
            })

        presentes = [c for c in saida if c["escutando"] or c["container"]]
        return {
            "host": host.name,
            "componentes": saida,
            "presentes": len(presentes),
            "vivos": sum(1 for c in saida if c["vivo"]),
            "duracao_ms": r.duration_ms,
        }
