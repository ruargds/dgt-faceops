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
        caminhos = " ".join(CAMINHOS_STATUS)
        nomes = "|".join(c["nome"] for c in COMPONENTES)
        portas_por_nome = " ".join(
            f"{c['nome']}={c['porta']}" for c in COMPONENTES
        )

        # A sondagem vai pela rede do Docker, não pelo localhost do host.
        # Os componentes do FindFace conversam entre containers e não
        # publicam porta na máquina — sondar 127.0.0.1 daria "não
        # respondeu" num servidor perfeitamente saudável.
        script = f"""
set +e
echo "{SEP}CONTAINERS"
docker ps --format '{{{{.Names}}}}|{{{{.Status}}}}' 2>/dev/null | grep -i -E '{nomes.replace("|", "|")}|findface|tarantool|ntls' | head -40

echo "{SEP}IPS"
for c in $(docker ps --format '{{{{.Names}}}}' 2>/dev/null | grep -i -E 'findface|tarantool|ntls' | head -40); do
  ip="$(docker inspect -f '{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}} {{{{end}}}}' "$c" 2>/dev/null | awk '{{print $1}}')"
  [ -n "$ip" ] && echo "$c|$ip"
done

echo "{SEP}ESCUTANDO"
(ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null) | awk '{{print $4}}' | sed 's/.*://' | sort -un | tr '\n' ' '
echo ""

echo "{SEP}SONDA"
for par in {portas_por_nome}; do
  nome="${{par%%=*}}"; porta="${{par##*=}}"
  # Container daquele componente e o IP dele na rede do compose.
  ct="$(docker ps --format '{{{{.Names}}}}' 2>/dev/null | grep -i "${{nome#findface-}}" | head -1)"
  alvos="127.0.0.1"
  if [ -n "$ct" ]; then
    ip="$(docker inspect -f '{{{{range .NetworkSettings.Networks}}}}{{{{.IPAddress}}}} {{{{end}}}}' "$ct" 2>/dev/null | awk '{{print $1}}')"
    [ -n "$ip" ] && alvos="$ip 127.0.0.1"
  fi
  achou=0
  for alvo in $alvos; do
    for cam in {caminhos}; do
      cod="$(curl -s -o /tmp/.faceops_body -w '%{{http_code}}' --max-time 3 "http://$alvo:$porta$cam" 2>/dev/null)"
      if [ -n "$cod" ] && [ "$cod" != "000" ]; then
        corpo="$(head -c 300 /tmp/.faceops_body 2>/dev/null | tr -d '\n' | tr -d '\r')"
        echo "$nome|$alvo|$cam|$cod|$corpo"
        achou=1
        break
      fi
    done
    [ "$achou" = "1" ] && break
  done
  [ "$achou" = "0" ] && echo "$nome|$alvos||000|"
done
rm -f /tmp/.faceops_body 2>/dev/null
echo "{SEP}FERRAMENTAS"
command -v curl >/dev/null 2>&1 && echo curl
echo "{SEP}FIM"
"""

        try:
            r = await self.ssh.run(host, script, sudo=True, timeout=180)
        except SSHError as exc:
            raise InternosError(f"não consegui consultar '{host.name}': {exc}") from exc

        s = _secoes(r.stdout or "")
        tem_curl = "curl" in (s.get("FERRAMENTAS") or "")

        containers: dict[str, str] = {}
        for linha in (s.get("CONTAINERS") or "").splitlines():
            if "|" in linha:
                nome, _, estado = linha.partition("|")
                containers[nome.strip()] = estado.strip()

        escutando = {
            int(p) for p in re.findall(r"\d+", s.get("ESCUTANDO", "")) if p.isdigit()
        }

        sondas: dict[str, dict] = {}
        for linha in (s.get("SONDA") or "").splitlines():
            partes = linha.split("|", 4)
            if len(partes) < 4:
                continue
            sondas[partes[0]] = {
                "alvo": partes[1],
                "caminho": partes[2],
                "codigo": partes[3],
                "corpo": partes[4] if len(partes) > 4 else "",
            }

        saida = []
        for comp in COMPONENTES:
            sonda = sondas.get(comp["nome"], {})
            codigo = sonda.get("codigo", "")
            container = next(
                (
                    f"{nome} ({estado})"
                    for nome, estado in containers.items()
                    if comp["nome"].replace("findface-", "") in nome
                ),
                "",
            )

            # 2xx/3xx é vivo. 401/403 também: o serviço respondeu, só não
            # deixou entrar sem credencial — informação boa, não falha.
            vivo = bool(codigo[:1] in ("2", "3") or codigo in ("401", "403"))

            # Sem resposta e sem porta local: pode ser serviço interno que
            # não fala HTTP (tarantool, por exemplo) ou rede que o host não
            # alcança. Isso NÃO é "parado" — é "não sondável daqui".
            sondavel = bool(codigo and codigo != "000") or comp["porta"] in escutando

            resumo = ""
            corpo = (sonda.get("corpo") or "").strip()
            if corpo.startswith("{"):
                try:
                    dados = json.loads(corpo)
                    resumo = ", ".join(
                        f"{k}={v}"
                        for k, v in list(dados.items())[:4]
                        if not isinstance(v, (dict, list))
                    )
                except json.JSONDecodeError:
                    resumo = corpo[:120]
            else:
                resumo = corpo[:120]

            saida.append({
                **comp,
                "escutando": comp["porta"] in escutando,
                "codigo": codigo if codigo != "000" else "",
                "caminho": sonda.get("caminho", ""),
                "alvo": sonda.get("alvo", ""),
                "resumo": resumo,
                "container": container,
                "vivo": vivo,
                "sondavel": sondavel,
            })

        presentes = [c for c in saida if c["container"] or c["escutando"]]
        return {
            "host": host.name,
            "componentes": saida,
            "presentes": len(presentes),
            "vivos": sum(1 for c in saida if c["vivo"]),
            "sondaveis": sum(1 for c in presentes if c["sondavel"]),
            "tem_curl": tem_curl,
            "duracao_ms": r.duration_ms,
        }
