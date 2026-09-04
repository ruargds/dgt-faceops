"""
Crescimento — o que está subindo, quando estoura, e quem está empurrando.

O painel já respondia "o que caiu" (incidentes), "o que causou" (apuração)
e "o que está fora do limite agora" (alertas). Faltava a pergunta que dá
**tempo de agir**: *isto está subindo sem parar — e vai me derrubar
quando?*

É um problema diferente dos outros três. Um limiar dispara quando o
estrago já está perto; uma queda se apura depois que aconteceu. Consumo
que dobra a cada duas horas passa por baixo dos dois: às 22h estava em
40% e ninguém viu nada; às 3h a máquina está morta.

## As três perguntas, nesta ordem

1. **Está subindo?** — sai das amostras que o ciclo do monitor **já**
   gravou. Custo zero de servidor: nenhuma ida nova, nenhum comando.
2. **Quando estoura, e o que quebra?** — projeção da taxa medida até o
   teto, com o dano escrito em termos de operação, não de métrica.
3. **Quem está empurrando?** — aí sim, UMA execução SSH, seletiva, e só
   depois que a subida se confirmou em vários ciclos.

Essa ordem é o desenho inteiro. Rastrear culpado é caro (`du` numa árvore
com milhões de fotos custa minutos de disco em produção), então só
acontece depois que a detecção — que é de graça — se confirmou. É a
diferença entre um diagnóstico e uma varredura permanente.

## O que impede isto de virar alarme falso

Quatro travas, cada uma de um jeito de errar já conhecido:

* **Poucos pontos não desenham tendência.** Abaixo de `min_pontos` a
  resposta é "indeterminado", nunca uma reta puxada de três amostras.
* **Reinício não é vazamento.** Container reiniciado devolve a memória de
  uma vez; log rotacionado devolve disco. A série é cortada nessas quedas
  e a análise usa só o trecho de depois. Duas quedas ou mais com subida
  entre elas viram `serrote` — que é outro diagnóstico ("volta ao normal
  quando reinicia"), e não um vazamento.
* **Cache não é uso.** A amostra já grava `total - MemAvailable`, então
  buffer e cache cheios não viram alarme (ver `metrics_service`).
* **Crescimento esperado não é defeito.** Foto de evento acumula porque
  gente passa na frente da câmera. O catálogo diz isso na cara, e a saída
  ali é decidir retenção, não caçar culpado.

## O que ele nunca faz

**Não age.** Não reinicia container, não apaga arquivo, não muda
configuração — nem quando a projeção diz que a máquina cai em duas horas.
A tela aponta e explica; quem decide é gente. Diagnóstico que age sozinho
é alarme de incêndio que abre a janela.

E o rastreio **só lê**: o comando não tem `rm`, `restart`, `truncate` nem
redirecionamento de escrita, e há teste que falha se alguém acrescentar.
"""
import logging
import math
import re
import shlex
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.models.amostra import Amostra
from app.models.amostra_container import AmostraContainer
from app.models.amostra_disco import AmostraDisco
from app.models.crescimento import Crescimento
from app.services.catalogo_crescimento import (
    NAO_E_VAZAMENTO, casar_caminho, casar_container,
)

log = logging.getLogger("faceops.crescimento")

SEP = "###FACEOPS:"

# ── Régua da detecção ──────────────────────────────────────────────────

# Menos pontos que isto não desenham tendência nenhuma. Com 8 pontos a
# 60 s a janela é de 8 minutos; com 8 pontos no modo econômico (300 s),
# de 40 min — nos dois casos é curto, e por isso a confiança também
# depende do R².
MIN_PONTOS = 8

# Queda que caracteriza reinício/rotação, em pontos percentuais. Abaixo
# disso é oscilação normal de carga; acima, alguma coisa devolveu recurso
# de uma vez só, e a série de antes não vale para projetar a de depois.
QUEDA_RESET_PP = 5.0

# A segunda metade da janela tem de crescer este tanto a mais que a
# primeira para o regime virar "acelerando" — que é o que o pedido chama
# de exponencial. 2x é o mínimo para não confundir com ruído de carga.
FATOR_ACELERACAO = 2.0

# Qualidade do ajuste que separa "tendência" de "nuvem de pontos".
R2_ALTA = 0.90
R2_MEDIA = 0.70

# Inclinação abaixo da qual a série está parada, em pontos percentuais
# por hora. Meio ponto por DIA — abaixo disso nenhum recurso chega ao
# teto num prazo que interesse a quem opera, e chamar de "subindo" o
# que a régua não distingue de ruído é o começo do alarme falso.
RUIDO_PP_H = 0.02

# Onde o estrago acontece, por recurso. NÃO são os limiares de alerta:
# limiar é quando avisar, isto é quando quebra.
#
# Memória: o OOM killer age antes dos 100% — sobra sempre um resto
# reservado ao kernel. Disco: o Postgres já degrada com pouca folga, e
# 100% é onde ele para de gravar de vez. Swap: não "estoura", mas passado
# disso a latência já é o problema.
TETO = {"memoria": 95.0, "disco": 95.0, "swap": 90.0}

# Quanto guardar da série numa vigilância. Sessenta medições cobrem uma
# hora a cada minuto ou cinco horas no modo econômico — o bastante para
# alguém conferir a conclusão. Guardar tudo faria a linha crescer sem
# teto, que é justamente o defeito que este arquivo existe para achar.
LIMITE_MEDICOES = 60

# Teto do rastreio seletivo, por passada do monitor. Cinco vigilâncias
# abrindo juntas não podem virar cinco execuções SSH.
MAX_RASTREIOS_POR_CICLO = 1

# Teto do comando de rastreio inteiro. Passado disso o que importa é o
# ciclo seguir; rastreio incompleto é dito como incompleto.
TIMEOUT_RASTREIO_S = 100

# Teto de cada `du`. Numa árvore com milhões de fotos de evento o `du`
# não termina — e "não medido" é resposta legítima, número inventado não.
SEGUNDOS_DU = 20

ROTULO = {"memoria": "memória", "disco": "disco", "swap": "swap"}


# ── Matemática da tendência ────────────────────────────────────────────


def _regressao(pontos: list[tuple[float, float]]) -> tuple[float, float]:
    """
    Mínimos quadrados: devolve (inclinação por hora, R²).

    R² 0 quando a série é plana ou não dá para calcular — e inclinação 0
    junto, para ninguém projetar em cima de uma reta que não existe.
    """
    n = len(pontos)
    if n < 2:
        return 0.0, 0.0
    mx = sum(t for t, _ in pontos) / n
    my = sum(v for _, v in pontos) / n
    sxx = sum((t - mx) ** 2 for t, _ in pontos)
    if sxx <= 0:
        return 0.0, 0.0
    sxy = sum((t - mx) * (v - my) for t, v in pontos)
    inclinacao = sxy / sxx
    total = sum((v - my) ** 2 for _, v in pontos)
    if total <= 0:
        return 0.0, 0.0
    resto = sum((v - (my + inclinacao * (t - mx))) ** 2 for t, v in pontos)
    return inclinacao, max(0.0, min(1.0, 1 - resto / total))


def _segmentos(pontos: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """
    Corta a série nas quedas bruscas.

    Um container reiniciado devolve a memória de uma vez; um log
    rotacionado devolve o disco. Ajustar uma reta por cima disso daria
    inclinação negativa numa máquina que está, sim, vazando — só que em
    dentes de serra.
    """
    if not pontos:
        return []
    partes: list[list[tuple[float, float]]] = [[pontos[0]]]
    for anterior, atual in zip(pontos, pontos[1:]):
        if atual[1] < anterior[1] - QUEDA_RESET_PP:
            partes.append([atual])
        else:
            partes[-1].append(atual)
    return partes


def _dobra(pontos: list[tuple[float, float]], r2_linear: float) -> float | None:
    """
    Em quantas horas dobra, pelo ajuste exponencial — quando ele explica
    melhor que a reta.

    Só devolve número quando o ajuste em escala logarítmica é ao menos tão
    bom quanto o linear E tem qualidade própria. Anunciar "dobra a cada
    2h" a partir de uma nuvem de pontos é inventar precisão, que é o mesmo
    defeito de "acaba em 77 dias" — a projeção que ignorava a retenção.
    """
    validos = [(t, math.log(v)) for t, v in pontos if v > 0]
    if len(validos) < MIN_PONTOS:
        return None
    k, r2 = _regressao(validos)
    if k <= 0 or r2 < max(R2_MEDIA, r2_linear):
        return None
    horas = math.log(2) / k
    # Abaixo de alguns minutos não é tendência, é degrau; acima de um mês
    # não é "dobrar", é rotina.
    return round(horas, 2) if 0.1 <= horas <= 720 else None


def analisar_serie(pontos: list[tuple[float, float]]) -> dict:
    """
    De uma série (horas, percentual) para um diagnóstico de tendência.

    Sem banco e sem SSH de propósito: é aqui que mora toda a decisão, e é
    aqui que os testes precisam alcançar.
    """
    vazio = {
        "regime": "indeterminado",
        "taxa_por_h": 0.0,
        "r2": 0.0,
        "confianca": "nenhuma",
        "dobra_h": None,
        "reinicios": 0,
        "pontos": len(pontos),
        "valor_inicial": pontos[0][1] if pontos else 0.0,
        "valor_atual": pontos[-1][1] if pontos else 0.0,
        "motivo": "",
    }

    if len(pontos) < MIN_PONTOS:
        vazio["motivo"] = (
            f"{len(pontos)} ponto(s) na janela — abaixo de {MIN_PONTOS} não "
            "dá para afirmar tendência"
        )
        return vazio

    partes = _segmentos(pontos)
    reinicios = len(partes) - 1
    trecho = partes[-1]

    # Sobe, cai, sobe de novo: o diagnóstico é outro. Não é vazamento
    # contínuo — é algo que volta ao normal quando reinicia, e a pergunta
    # vira "por que reinicia".
    if reinicios >= 2 and all(_regressao(p)[0] > 0 for p in partes if len(p) >= 3):
        inclinacao, r2 = _regressao(trecho)
        return {
            **vazio,
            "regime": "serrote",
            "taxa_por_h": round(max(0.0, inclinacao), 3),
            "r2": round(r2, 3),
            "confianca": "media",
            "reinicios": reinicios,
            "valor_atual": trecho[-1][1],
            "valor_inicial": trecho[0][1],
            "motivo": (
                f"{reinicios} queda(s) bruscas na janela com subida entre elas "
                "— o recurso volta ao normal quando algo reinicia"
            ),
        }

    if len(trecho) < MIN_PONTOS:
        vazio["reinicios"] = reinicios
        vazio["motivo"] = (
            "houve queda brusca na janela (reinício ou rotação) e o trecho "
            f"depois dela tem só {len(trecho)} ponto(s)"
        )
        return vazio

    inclinacao, r2 = _regressao(trecho)
    valor_inicial, valor_atual = trecho[0][1], trecho[-1][1]

    if inclinacao <= RUIDO_PP_H:
        return {
            **vazio,
            # Parada é diferente de recuando: a primeira não pede nada, a
            # segunda costuma ser alguém tendo resolvido o problema.
            "regime": "estavel" if inclinacao > -RUIDO_PP_H else "recuando",
            "taxa_por_h": round(inclinacao, 3),
            "r2": round(r2, 3),
            "confianca": "alta" if r2 >= R2_ALTA else "media",
            "reinicios": reinicios,
            "valor_inicial": valor_inicial,
            "valor_atual": valor_atual,
        }

    # Acelera? Compara a taxa da segunda metade com a da primeira. É a
    # pergunta do pedido: "do nada começou a subir mais rápido".
    meio = len(trecho) // 2
    taxa1, _ = _regressao(trecho[:meio + 1])
    taxa2, _ = _regressao(trecho[meio:])
    acelera = taxa1 > 0 and taxa2 >= FATOR_ACELERACAO * taxa1
    dobra = _dobra(trecho, r2) if acelera else None

    # Nuvem de pontos não é tendência. Uma série que oscila em torno do
    # mesmo valor tem inclinação positiva minúscula e R² perto de zero —
    # chamar isso de "subindo" é o defeito de "acaba em 77 dias" de novo,
    # em outro lugar: precisão que a medida não sustenta.
    #
    # A exceção é a aceleração com ajuste exponencial bom: uma curva que
    # dispara no fim tem R² linear ruim JUSTAMENTE porque não é uma reta,
    # e é o caso que este arquivo existe para pegar.
    if r2 < R2_MEDIA and not (acelera and dobra is not None):
        return {
            **vazio,
            "taxa_por_h": round(inclinacao, 3),
            "r2": round(r2, 3),
            "reinicios": reinicios,
            "pontos": len(trecho),
            "valor_inicial": valor_inicial,
            "valor_atual": valor_atual,
            "motivo": (
                f"os pontos não formam tendência (R² {r2:.2f}) — a variação "
                "está no ruído da medição, não numa subida"
            ),
        }

    if r2 >= R2_ALTA and len(trecho) >= MIN_PONTOS + 4:
        confianca = "alta"
    elif r2 >= R2_MEDIA:
        confianca = "media"
    else:
        confianca = "baixa"

    return {
        "regime": "acelerando" if acelera else "linear",
        "taxa_por_h": round(inclinacao, 3),
        "r2": round(r2, 3),
        "confianca": confianca,
        "dobra_h": dobra,
        "reinicios": reinicios,
        "pontos": len(trecho),
        "valor_inicial": valor_inicial,
        "valor_atual": valor_atual,
        "motivo": "",
    }


def projetar(valor_atual: float, taxa_por_h: float, teto: float) -> float | None:
    """
    Horas até encostar no teto, pela taxa medida.

    `None` quando não sobe (não há o que projetar) ou quando já passou do
    teto — nesse caso não é previsão, é o presente, e quem chama diz isso
    com outras palavras.
    """
    if taxa_por_h <= 0 or valor_atual >= teto:
        return None
    return round((teto - valor_atual) / taxa_por_h, 2)


def _quando(horas: float | None) -> str:
    """Horas em texto de gente: '3h 20min', '2 dias'."""
    if horas is None:
        return ""
    if horas < 1:
        return f"{int(horas * 60)}min"
    if horas < 48:
        h = int(horas)
        m = int((horas - h) * 60)
        return f"{h}h {m}min" if m else f"{h}h"
    return f"{horas / 24:.1f} dias".replace(".", ",")


def _gb(bytes_: float) -> str:
    gb = (bytes_ or 0) / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB".replace(".", ",")
    return f"{(bytes_ or 0) / (1024 ** 2):.0f} MB"


# ── O rastreio seletivo ────────────────────────────────────────────────
#
# Duas listas de comandos, uma por recurso, porque as perguntas são
# diferentes e o custo também: a de memória é toda barata (lê /proc e o
# docker), a de disco tem `du`, que é o comando caro deste painel.
#
# Nenhum comando aqui altera estado. É deliberado, e há teste que falha
# se alguém acrescentar um: isto roda com sudo, em servidor de produção,
# justamente quando ele está sob pressão de recurso.


def script_memoria() -> str:
    """Quem está com a memória — processos, containers e o que não é processo."""
    return f"""
set +e
echo "{SEP}MEMINFO"
cat /proc/meminfo 2>/dev/null | head -20
echo "{SEP}PS"
ps -eo pid,rss,etimes,comm --sort=-rss 2>/dev/null | head -13
echo "{SEP}CGROUP"
# Container dono dos PIDs que mais ocupam. Lê arquivo local — `docker top`
# por container custaria uma execução por container.
for p in $(ps -eo pid --sort=-rss 2>/dev/null | sed -n '2,13p'); do
  c="$(tr '\\0' ' ' < /proc/$p/cgroup 2>/dev/null | grep -oE '[0-9a-f]{{12,64}}' | head -1)"
  [ -n "$c" ] && echo "$p|$c"
done
echo "{SEP}CTNAMES"
if command -v docker >/dev/null 2>&1; then
  docker ps --no-trunc --format '{{{{.ID}}}}|{{{{.Names}}}}' 2>/dev/null
fi
echo "{SEP}STATS"
if command -v docker >/dev/null 2>&1; then
  timeout 25 docker stats --no-stream --format '{{{{.Name}}}}|{{{{.MemUsage}}}}|{{{{.MemPerc}}}}' 2>/dev/null
fi
echo "{SEP}LIMITES"
if command -v docker >/dev/null 2>&1; then
  for c in $(docker ps -q 2>/dev/null | head -40); do
    docker inspect "$c" --format '{{{{.Name}}}}|{{{{.HostConfig.Memory}}}}|{{{{.RestartCount}}}}' 2>/dev/null
  done
fi
echo "{SEP}TMPFS"
# tmpfs ocupa MEMÓRIA, não disco. Arquivo esquecido em /dev/shm aparece
# como memória sumindo sem processo nenhum para culpar.
df -B1 -P -t tmpfs 2>/dev/null | head -8
echo "{SEP}OOM"
(journalctl -k --since '-24 hours' --no-pager 2>/dev/null | grep -iE 'out of memory|oom-kill' | tail -5) \
  || (dmesg -T 2>/dev/null | grep -iE 'out of memory|oom-kill' | tail -5)
echo "{SEP}FIM"
"""


def script_disco(ff_dir: str, ponto: str, janela_h: int, staging: str) -> str:
    """
    Quem está com o disco — por caminho, por arquivo recente e o que foi
    apagado mas continua ocupando.

    Todo `du` tem `timeout` e prioridade baixa de E/S: num disco com teto
    de IOPS, o diagnóstico não pode virar a causa do próximo incidente
    (ver `docs/33_SATURACAO_DE_DISCO.md`).
    """
    q_ff = shlex.quote(ff_dir or "/opt/findface-multi")
    q_ponto = shlex.quote(ponto or "/")
    q_staging = shlex.quote(staging or "/var/backups/faceops")
    horas = max(1, int(janela_h))

    return f"""
set +e
if command -v ionice >/dev/null 2>&1; then BAIXO="ionice -c3 nice -n19"; else BAIXO="nice -n19"; fi
echo "{SEP}DF"
df -B1 -P -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null
echo "{SEP}INODES"
df -i -P -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null
echo "{SEP}CAMINHOS"
# Lista curta e fixa: os lugares que já encheram disco neste ambiente.
# Varrer o disco inteiro custaria minutos de E/S em produção.
for d in /var/log /var/lib/docker/containers {q_ff}/data {q_staging}; do
  [ -d "$d" ] && echo "$d|$(timeout {SEGUNDOS_DU} $BAIXO du -sb "$d" 2>/dev/null | cut -f1)"
done
echo "{SEP}NIVEL1"
timeout {SEGUNDOS_DU} $BAIXO du -b --max-depth=1 {q_ff}/data 2>/dev/null | sort -rn | head -12
echo "{SEP}JOURNAL"
journalctl --disk-usage 2>/dev/null
echo "{SEP}RECENTES"
# Arquivo grande que MUDOU na janela: é o que está crescendo agora, e sai
# mais barato que percorrer a árvore inteira somando tamanho.
timeout {SEGUNDOS_DU} $BAIXO find {q_ponto} -xdev -type f -size +50M -newermt '-{horas} hours' \
  -printf '%s|%p\\n' 2>/dev/null | sort -rn | head -12
echo "{SEP}APAGADOS"
# Apagado com `rm` e ainda aberto: o `du` não acha e o `df` continua
# cheio. É a pista que manda a investigação para o lugar certo.
(timeout 15 lsof -nP +L1 2>/dev/null | head -8) \
  || (find /proc/[0-9]*/fd -type l -lname '*(deleted)*' 2>/dev/null | head -8)
echo "{SEP}FIM"
"""


def _secoes(saida: str) -> dict[str, str]:
    """Mesmo formato de seção do resto do painel."""
    secoes: dict[str, str] = {}
    atual = None
    buf: list[str] = []
    for linha in (saida or "").splitlines():
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


_TAMANHO = re.compile(r"^([\d.]+)\s*([KMGTP]?i?B)$", re.I)
_FATOR = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4, "P": 1024 ** 5}


def _bytes(texto: str) -> int:
    """'1.5GiB' → bytes. Mesmo formato do `docker stats`."""
    m = _TAMANHO.match((texto or "").strip())
    if not m:
        return 0
    unidade = m.group(2).upper().replace("I", "").replace("B", "")
    return int(float(m.group(1)) * _FATOR.get(unidade, 1))


def interpretar_memoria(secoes: dict[str, str]) -> dict:
    """
    Do texto bruto para "quem está com a memória".

    Devolve `alvos` (container → bytes) além dos achados: são eles que,
    comparados com o rastreio anterior, dizem quem CRESCEU — que é outra
    pergunta, e a que interessa aqui.
    """
    achados: list[dict] = []
    alvos: dict[str, int] = {}

    containers: list[tuple[str, int, float]] = []
    for linha in (secoes.get("STATS") or "").splitlines():
        partes = linha.split("|")
        if len(partes) < 2:
            continue
        nome = partes[0].strip()
        usado = _bytes(partes[1].split("/")[0])
        pct = 0.0
        if len(partes) > 2:
            try:
                pct = float(partes[2].replace("%", "").strip() or 0)
            except ValueError:
                pct = 0.0
        if nome and usado:
            containers.append((nome, usado, pct))
            alvos[nome] = usado

    containers.sort(key=lambda c: c[1], reverse=True)
    for nome, usado, _pct in containers[:5]:
        achados.append({
            "fonte": "docker",
            "alvo": nome,
            "texto": f"{nome} está com {_gb(usado)} de memória",
        })

    # Container sem teto: sem `mem_limit`, quem vaza derruba a máquina
    # inteira em vez de só a si mesmo — e o kernel pode escolher outra
    # vítima.
    sem_teto = []
    for linha in (secoes.get("LIMITES") or "").splitlines():
        partes = linha.split("|")
        if len(partes) < 2:
            continue
        nome = partes[0].strip().lstrip("/")
        try:
            limite = int(partes[1].strip() or 0)
        except ValueError:
            continue
        if limite == 0 and nome:
            sem_teto.append(nome)
    if sem_teto and containers:
        maior = containers[0][0]
        if maior.lstrip("/") in sem_teto:
            achados.append({
                "fonte": "docker",
                "alvo": maior,
                "texto": f"{maior} roda sem limite de memória — crescendo, "
                         "ele derruba a máquina inteira, e o kernel pode "
                         "matar outro serviço no lugar dele",
            })

    # Processo fora de container (o FindFace não é tudo que roda na VM).
    for linha in (secoes.get("PS") or "").splitlines()[1:6]:
        partes = linha.split()
        if len(partes) < 4:
            continue
        try:
            rss = int(partes[1]) * 1024
        except ValueError:
            continue
        comando = partes[3]
        if rss >= 512 * 1024 ** 2:
            achados.append({
                "fonte": "processo",
                "alvo": comando,
                "texto": f"{comando} (pid {partes[0]}) ocupa {_gb(rss)}",
            })

    # tmpfs conta como memória — arquivo esquecido em /dev/shm some do
    # `ps` e do `docker stats`, e é memória que ninguém acha.
    for linha in (secoes.get("TMPFS") or "").splitlines()[1:]:
        partes = linha.split()
        if len(partes) < 6:
            continue
        try:
            usado = int(partes[2])
        except ValueError:
            continue
        if usado >= 256 * 1024 ** 2:
            achados.append({
                "fonte": "tmpfs",
                "alvo": partes[5],
                "texto": f"{partes[5]} (tmpfs) tem {_gb(usado)} em arquivo — "
                         "tmpfs ocupa MEMÓRIA, não disco",
            })
            alvos[f"tmpfs:{partes[5]}"] = usado

    for linha in (secoes.get("OOM") or "").splitlines()[:3]:
        if linha.strip():
            achados.append({
                "fonte": "kernel",
                "alvo": "",
                "texto": linha.strip()[:300],
            })

    return {"alvos": alvos, "achados": achados}


def interpretar_disco(secoes: dict[str, str]) -> dict:
    """Do texto bruto para "quem está com o disco"."""
    achados: list[dict] = []
    alvos: dict[str, int] = {}

    for linha in (secoes.get("CAMINHOS") or "").splitlines():
        caminho, _, valor = linha.partition("|")
        try:
            tamanho = int((valor or "").strip())
        except ValueError:
            # `du` estourou o prazo. "Não medido" é resposta; zero não é.
            achados.append({
                "fonte": "du",
                "alvo": caminho.strip(),
                "texto": f"{caminho.strip()} não terminou de ser medido em "
                         f"{SEGUNDOS_DU}s — numa árvore com milhões de fotos "
                         "isso é normal, e o número fica sem resposta",
            })
            continue
        if caminho.strip():
            alvos[caminho.strip()] = tamanho
            achados.append({
                "fonte": "du",
                "alvo": caminho.strip(),
                "texto": f"{caminho.strip()} ocupa {_gb(tamanho)}",
            })

    for linha in (secoes.get("NIVEL1") or "").splitlines()[:6]:
        partes = linha.split(None, 1)
        if len(partes) != 2:
            continue
        try:
            tamanho = int(partes[0])
        except ValueError:
            continue
        caminho = partes[1].strip()
        alvos.setdefault(caminho, tamanho)

    journal = (secoes.get("JOURNAL") or "").strip()
    if journal:
        achados.append({"fonte": "journal", "alvo": "/var/log/journal",
                        "texto": journal[:200]})

    for linha in (secoes.get("RECENTES") or "").splitlines()[:6]:
        tamanho, _, caminho = linha.partition("|")
        try:
            n = int(tamanho.strip())
        except ValueError:
            continue
        achados.append({
            "fonte": "arquivo recente",
            "alvo": caminho.strip(),
            "texto": f"{caminho.strip()} — {_gb(n)}, modificado dentro da janela",
        })

    apagados = [l for l in (secoes.get("APAGADOS") or "").splitlines() if l.strip()]
    if apagados:
        achados.append({
            "fonte": "apagado e aberto",
            "alvo": "(deleted)",
            "texto": "há arquivo apagado que algum processo ainda mantém "
                     "aberto — o espaço só volta quando esse processo "
                     f"fechar. Primeiro: {apagados[0].strip()[:160]}",
        })

    # Inodes: acabar inode com disco sobrando devolve "No space left on
    # device" e manda todo mundo olhar o `df -h`, onde não há nada.
    for linha in (secoes.get("INODES") or "").splitlines()[1:]:
        partes = linha.split()
        if len(partes) < 6:
            continue
        try:
            total, usado = int(partes[1]), int(partes[2])
        except ValueError:
            continue
        if total > 0 and usado / total >= 0.85:
            achados.append({
                "fonte": "inodes",
                "alvo": "inodes",
                "texto": f"{partes[5]} usou {usado / total * 100:.0f}% dos "
                         "inodes — o disco enche por quantidade de arquivos, "
                         "não por tamanho",
            })

    return {"alvos": alvos, "achados": achados}


def atribuir(medicoes: list[dict]) -> list[dict]:
    """
    Quem CRESCEU entre dois rastreios — a pergunta que um retrato sozinho
    não responde.

    Um `du` diz que `data/` tem 800 GB, o que não acusa ninguém: ele
    sempre teve. Dois `du` com hora dizem que `data/` ganhou 40 GB em
    duas horas, e aí sim há um culpado com número.

    Devolve só quem cresceu, do maior para o menor. Lista vazia é
    resposta: significa "nada mudou o bastante entre as duas leituras".
    """
    comparaveis = [m for m in (medicoes or []) if m.get("alvos") and m.get("ts")]
    if len(comparaveis) < 2:
        return []

    antes, depois = comparaveis[-2], comparaveis[-1]
    try:
        t0 = datetime.fromisoformat(antes["ts"])
        t1 = datetime.fromisoformat(depois["ts"])
    except (ValueError, TypeError):
        return []
    horas = (t1 - t0).total_seconds() / 3600
    if horas <= 0:
        return []

    saida = []
    for alvo, valor in (depois["alvos"] or {}).items():
        anterior = (antes["alvos"] or {}).get(alvo)
        if anterior is None:
            continue
        delta = valor - anterior
        if delta <= 0:
            continue
        saida.append({
            "alvo": alvo,
            "cresceu_bytes": int(delta),
            "por_hora_bytes": int(delta / horas),
            "horas": round(horas, 2),
        })
    saida.sort(key=lambda x: x["por_hora_bytes"], reverse=True)
    return saida


class CrescimentoService:
    """
    Detecta pelas amostras, projeta o estrago e — só então — rastreia o
    culpado no servidor.
    """

    def __init__(self, ssh, config=None) -> None:
        self.ssh = ssh
        self.config = config
        # Quantos ciclos seguidos cada (host, recurso) apareceu subindo.
        # Em memória de propósito: é estado de detecção, não histórico.
        # Painel reiniciado recomeça a contagem, e o pior que acontece é
        # a vigilância abrir alguns minutos mais tarde.
        self._suspeitas: dict[tuple[int, str], int] = {}
        # Mesma ideia, do lado de fechar: quantos ciclos seguidos uma
        # vigilância ABERTA já leu "não preocupa mais". Sem isto, a
        # reavaliação em janela curta (`_recente`) fechava no primeiro
        # ciclo em que o ruído de arredondamento (a leitura vem com 1
        # casa decimal) esconde a inclinação real — abrindo e fechando de
        # novo minutos depois, para um disco cuja taxa real de 0,15 a
        # 0,6 ponto por hora nunca mudou.
        self._suspeitas_fechamento: dict[tuple[int, str], int] = {}
        # Quando cada host teve a última gravação de container. A cadência
        # é própria (padrão 5 min) porque o ciclo roda a cada 60 s e
        # memória de container não muda de forma interessante nesse ritmo
        # — gravar por ciclo seria 172 mil linhas por dia para desenhar a
        # mesma curva.
        self._ultima_gravacao: dict[int, datetime] = {}
        # Mesma ideia, para a série por dispositivo de disco — cadência
        # própria, separada da de containers.
        self._ultima_gravacao_disco: dict[int, datetime] = {}

    def _cfg(self, chave: str, padrao):
        if self.config is None:
            return padrao
        try:
            return self.config.get(chave)
        except (KeyError, ValueError, TypeError):
            return padrao

    def habilitado(self) -> bool:
        return bool(self._cfg("crescimento.ativo", True))

    # ── Régua configurada ──────────────────────────────────────────────

    def piso(self, recurso: str) -> float:
        """
        Taxa mínima, em pontos percentuais por hora, para valer atenção.

        São dois números porque as escalas não têm nada a ver: memória que
        sobe 2 pp/h estoura no mesmo turno; disco que sobe 2 pp/h já
        seria catástrofe — lá o normal se mede em pontos por DIA.
        """
        if recurso == "disco":
            return float(self._cfg("crescimento.disco_pp_por_dia", 1)) / 24.0
        return float(self._cfg("crescimento.mem_pp_por_h", 2))

    def horizonte_h(self) -> float:
        return float(self._cfg("crescimento.horizonte_h", 72))

    # ── Detecção: só banco, custo zero de servidor ─────────────────────

    @staticmethod
    def _intervalo(
        janela_h: float | None = None,
        de: datetime | None = None,
        ate: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        """
        (início, fim) a partir de uma janela relativa OU de um intervalo
        absoluto.

        Absoluto ganha quando os dois vêm: é o que a pessoa digitou, e
        janela relativa é sempre um atalho para o mesmo par. Fim sem
        início vira "as N horas até aquele instante" — é o que permite
        andar para trás no tempo sem perder o tamanho da janela.
        """
        agora = datetime.now(timezone.utc)
        fim = ate or agora
        if fim.tzinfo is None:
            fim = fim.replace(tzinfo=timezone.utc)
        if de is not None:
            inicio = de if de.tzinfo else de.replace(tzinfo=timezone.utc)
        else:
            inicio = fim - timedelta(hours=float(janela_h or 6))
        if inicio >= fim:
            # Intervalo invertido ou nulo: cai numa hora até o fim pedido,
            # em vez de devolver série vazia sem explicar por quê.
            inicio = fim - timedelta(hours=1)
        return inicio, fim

    async def _mais_antiga(self, db, modelo, host_id: int) -> datetime | None:
        """
        A amostra mais velha que existe para este host.

        É o que permite a tela dizer "não há dado tão para trás" em vez de
        desenhar um gráfico vazio e deixar a pessoa achando que o servidor
        ficou parado. A retenção configurada diz o teto teórico; isto diz
        o que de fato está lá.
        """
        from sqlalchemy import func

        r = await db.execute(
            select(func.min(modelo.ts)).where(modelo.host_id == host_id)
        )
        return r.scalar()

    async def _serie(
        self, db, host_id: int, janela_h: float | None = None,
        de: datetime | None = None, ate: datetime | None = None,
    ) -> list[Amostra]:
        desde, ateh = self._intervalo(janela_h, de, ate)
        r = await db.execute(
            select(
                Amostra.ts, Amostra.mem_pct, Amostra.disco_pct, Amostra.swap_pct,
                Amostra.mem_usado_mb, Amostra.mem_total_mb,
                Amostra.disco_livre_gb, Amostra.disco_total_gb, Amostra.disco_ponto,
            )
            .where(
                Amostra.host_id == host_id,
                Amostra.ts >= desde,
                Amostra.ts <= ateh,
                # Amostra de falha não entra na série: buraco na leitura
                # não é queda de consumo.
                Amostra.erro == "",
            )
            .order_by(Amostra.ts)
        )
        return list(r.all())

    @staticmethod
    def _pontos(linhas, campo: str) -> list[tuple[float, float]]:
        if not linhas:
            return []
        base = linhas[0].ts
        pontos = []
        for linha in linhas:
            valor = float(getattr(linha, campo, 0) or 0)
            # Zero só existe onde a métrica não foi medida (host sem swap,
            # coleta antiga). Tratar como valor real inventaria uma queda.
            if valor <= 0:
                continue
            horas = (linha.ts - base).total_seconds() / 3600
            pontos.append((horas, valor))
        return pontos

    @staticmethod
    def _recente(pontos: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """
        O fim da janela — o bastante para responder "ainda está subindo?".

        Um terço da série, nunca menos que o mínimo de pontos: menos que
        isso não sustenta conclusão nenhuma, e é justamente aqui que a
        conclusão FECHA uma vigilância.
        """
        if len(pontos) <= MIN_PONTOS:
            return pontos
        return pontos[-max(MIN_PONTOS, len(pontos) // 3):]

    def avaliar(self, recurso: str, pontos: list[tuple[float, float]]) -> dict:
        """
        Tendência + projeção + veredito de "isto preocupa?".

        Separado do banco para poder ser testado com uma lista de números.
        """
        analise = analisar_serie(pontos)
        teto = TETO.get(recurso, 95.0)
        horas_ate = projetar(analise["valor_atual"], analise["taxa_por_h"], teto)

        subindo = analise["regime"] in ("linear", "acelerando")
        acima_do_piso = analise["taxa_por_h"] >= self.piso(recurso)
        # Confiança baixa aparece na tela sob demanda, mas não abre
        # vigilância nem manda aviso: alarme falso permanente ensina a
        # ignorar a tela.
        confiavel = analise["confianca"] in ("alta", "media")

        preocupa = bool(
            subindo and acima_do_piso and confiavel
            and (
                analise["regime"] == "acelerando"
                or (horas_ate is not None and horas_ate <= self.horizonte_h())
            )
        )

        if analise["valor_atual"] >= teto:
            nivel = "critico"
        elif horas_ate is not None and horas_ate <= 6:
            nivel = "critico"
        elif analise["regime"] == "acelerando":
            nivel = "critico" if (horas_ate or 999) <= 24 else "atencao"
        else:
            nivel = "atencao"

        return {
            **analise,
            "recurso": recurso,
            "teto_pct": teto,
            "horas_ate_teto": horas_ate,
            "preocupa": preocupa,
            "nivel": nivel,
        }

    # ── O que quebra quando encostar ───────────────────────────────────

    @staticmethod
    def dano(recurso: str, culpado: str = "", ponto: str = "") -> str:
        """
        O estrago previsto, em termos de operação.

        Escrito aqui, e não no catálogo, porque não depende de qual causa
        é: quem enche o disco muda, o que acontece quando ele enche, não.
        """
        if recurso == "disco":
            onde = f" em {ponto}" if ponto else ""
            return (
                f"Quando o disco{onde} encher, o banco para de gravar e as "
                "passagens deixam de ser registradas. O reconhecimento "
                "continua rodando e nada é salvo — não há erro visível na "
                "operação."
            )
        if recurso == "swap":
            return (
                "Swap em uso não derruba nada: deixa lento. O caminho do "
                "reconhecimento passa a esperar disco, e em servidor com "
                "teto de IOPS isso soma com o banco e piora tudo junto."
            )
        quem = f" — hoje o maior é {culpado}" if culpado else ""
        return (
            f"Ao encostar no limite, o kernel mata o container que estiver "
            f"com mais memória{quem}. O serviço morto para sem avisar: as "
            "câmeras dele deixam de ser reconhecidas e não aparece erro na "
            "tela, só ausência de evento."
        )

    # ── Rastreio: a única parte que custa SSH ──────────────────────────

    async def rastrear(self, host, recurso: str, ponto: str = "") -> dict:
        """
        Uma execução SSH, seletiva, com tudo cercado por `timeout`.

        Nunca levanta: rastreio que derruba o ciclo do monitor custaria a
        amostra, que vale mais. Falha vira achado dizendo que falhou.
        """
        from app.core.config import settings

        janela = int(self._cfg("crescimento.janela_h", 6))
        if recurso == "disco":
            script = script_disco(
                host.ffmulti_dir or "/opt/findface-multi", ponto, janela,
                settings.REMOTE_STAGING_DIR,
            )
        else:
            script = script_memoria()

        try:
            r = await self.ssh.run_script(
                host, script, sudo=True, timeout=TIMEOUT_RASTREIO_S
            )
            secoes = _secoes(r.stdout or "")
        except Exception as exc:
            log.warning("rastreio de crescimento em %s falhou: %s", host.name, exc)
            return {
                "recurso": recurso,
                "achados": [{
                    "fonte": "rastreio",
                    "alvo": "",
                    "texto": f"a leitura no servidor falhou: {str(exc)[:200]}",
                }],
                "alvos": {},
                "culpado": "",
                "causa": "",
                "completo": False,
                "em": datetime.now(timezone.utc).isoformat(),
            }

        lido = (
            interpretar_disco(secoes) if recurso == "disco"
            else interpretar_memoria(secoes)
        )

        # O maior ocupante é o primeiro suspeito — mas quem cresceu entre
        # dois rastreios ganha dele, e essa comparação é feita em
        # `registrar_ciclo`, onde a série anterior está disponível.
        culpado = ""
        if lido["alvos"]:
            culpado = max(lido["alvos"].items(), key=lambda kv: kv[1])[0]

        causa = (
            casar_caminho(culpado) if recurso == "disco"
            else casar_container(culpado, "memoria")
        )

        return {
            "recurso": recurso,
            "achados": lido["achados"],
            "alvos": lido["alvos"],
            "culpado": culpado,
            "causa": (causa or {}).get("chave", ""),
            "completo": "FIM" in secoes,
            "em": datetime.now(timezone.utc).isoformat(),
        }

    # ── Ciclo: abre, mantém e fecha sozinho ────────────────────────────

    async def registrar_ciclo(self, db, host, host_ok: bool = True) -> list[dict]:
        """
        Chamado pelo ciclo do monitor, depois da amostra ter sido gravada.

        Devolve eventos no mesmo formato do `IncidenteService`, para o
        despacho de aviso ser o mesmo caminho — um formato de evento só.

        Servidor sem contato **não mexe em vigilância aberta**: sem
        alcançar a máquina não se sabe se o consumo caiu ou se só parou de
        ser medido, e fechar ali registraria uma melhora que ninguém
        observou. É a mesma regra do incidente de serviço.
        """
        if not self.habilitado() or not host_ok:
            return []

        janela = float(self._cfg("crescimento.janela_h", 6))
        linhas = await self._serie(db, host.id, janela)
        if len(linhas) < MIN_PONTOS:
            return []

        ponto = (linhas[-1].disco_ponto or "") if linhas else ""
        abertas = {
            c.recurso: c
            for c in (await db.execute(
                select(Crescimento).where(
                    Crescimento.host_id == host.id, Crescimento.fim.is_(None)
                )
            )).scalars().all()
        }

        eventos: list[dict] = []
        rastreios = 0

        for recurso, campo in (
            ("memoria", "mem_pct"), ("disco", "disco_pct"), ("swap", "swap_pct"),
        ):
            pontos = self._pontos(linhas, campo)
            veredito = self.avaliar(recurso, pontos)
            chave = (host.id, recurso)
            aberta = abertas.get(recurso)

            # Abrir pede evidência da janela inteira; MANTER aberta pede a
            # pergunta do presente — "ainda está subindo?". Sem essa
            # distinção, uma vigilância só fecharia quando a subida
            # saísse da janela: alguém resolveria o problema às 14h e a
            # tela continuaria acusando até as 20h, que é a definição de
            # alarme que ninguém acredita.
            if aberta is not None:
                veredito = self.avaliar(recurso, self._recente(pontos))

            if not veredito["preocupa"]:
                self._suspeitas.pop(chave, None)
                if aberta is not None:
                    # Simétrico ao "abrir": uma leitura não fecha
                    # vigilância. Só depois de N ciclos seguidos sem
                    # preocupação é que o "estabilizou" é publicado —
                    # antes disso, é só ruído de arredondamento e a
                    # vigilância continua aberta, calada.
                    self._suspeitas_fechamento[chave] = (
                        self._suspeitas_fechamento.get(chave, 0) + 1
                    )
                    minimo_fechar = int(self._cfg("crescimento.ciclos_para_fechar", 3))
                    if self._suspeitas_fechamento[chave] < minimo_fechar:
                        continue
                    self._suspeitas_fechamento.pop(chave, None)
                    eventos.append(self._fechar(host, aberta, veredito))
                continue

            if aberta is None:
                # Uma leitura não abre vigilância. "Do nada subiu" pode ser
                # um backup começando, uma câmera religando ou o cache do
                # próprio kernel — e vigilância que abre e fecha a cada
                # ciclo é ruído com cara de diagnóstico.
                self._suspeitas[chave] = self._suspeitas.get(chave, 0) + 1
                minimo = int(self._cfg("crescimento.ciclos_para_abrir", 3))
                if self._suspeitas[chave] < minimo:
                    continue
                self._suspeitas.pop(chave, None)
                nova = await self._abrir(db, host, veredito, ponto)
                # Primeiro o que é de graça: se a série por container já
                # sabe quem cresceu, o aviso sai com nome antes de
                # qualquer SSH.
                await self.culpar_por_serie(db, host, nova)
                if (
                    rastreios < MAX_RASTREIOS_POR_CICLO
                    and bool(self._cfg("crescimento.rastrear_sozinho", True))
                ):
                    await self.rastrear_e_gravar(db, host, nova, ponto)
                    rastreios += 1
                eventos.append(self._evento(host, nova, "crescimento"))
                continue

            # Chegou até aqui: preocupa de novo. Zera a contagem de
            # fechamento — a subida não parou o suficiente para justificar
            # o "estabilizou" que estava sendo acumulado.
            self._suspeitas_fechamento.pop(chave, None)

            # Já aberta: atualiza a projeção e re-rastreia quando vence o
            # intervalo. É a "busca persistente" — o culpado do primeiro
            # rastreio pode não ser o mesmo de duas horas depois.
            subiu = self._atualizar(aberta, veredito)
            venceu = self._rastreio_vencido(aberta)
            if venceu:
                # De graça e a cada intervalo: o container que empurra
                # hoje pode não ser o de duas horas atrás.
                await self.culpar_por_serie(db, host, aberta)
            if (
                venceu and rastreios < MAX_RASTREIOS_POR_CICLO
                and bool(self._cfg("crescimento.rastrear_sozinho", True))
            ):
                await self.rastrear_e_gravar(db, host, aberta, ponto)
                rastreios += 1
            # Aviso repetido só quando a situação PIOROU de nível. Repetir
            # a cada ciclo o mesmo "está subindo" é o caminho mais curto
            # para o grupo silenciar o bot.
            if subiu:
                eventos.append(self._evento(host, aberta, "crescimento"))

        return eventos

    # ── Peças do ciclo ─────────────────────────────────────────────────

    async def _abrir(self, db, host, veredito: dict, ponto: str) -> Crescimento:
        agora = datetime.now(timezone.utc)
        horas = veredito["horas_ate_teto"]
        nova = Crescimento(
            host_id=host.id,
            recurso=veredito["recurso"],
            regime=veredito["regime"],
            nivel=veredito["nivel"],
            confianca=veredito["confianca"],
            inicio=agora,
            valor_inicial=round(veredito["valor_inicial"], 1),
            valor_atual=round(veredito["valor_atual"], 1),
            taxa_por_h=veredito["taxa_por_h"],
            dobra_h=veredito["dobra_h"],
            teto_pct=veredito["teto_pct"],
            estouro_em=(agora + timedelta(hours=horas)) if horas else None,
            ciclos=1,
            medicoes=[{
                "ts": agora.isoformat(),
                "valor": round(veredito["valor_atual"], 1),
                "taxa_por_h": veredito["taxa_por_h"],
                "regime": veredito["regime"],
            }],
        )
        db.add(nova)
        await db.flush()
        log.info(
            "vigilância de %s aberta em %s (%.2f pp/h, %s)",
            nova.recurso, host.name, nova.taxa_por_h, nova.regime,
        )
        return nova

    def _atualizar(self, vig: Crescimento, veredito: dict) -> bool:
        """Atualiza a vigilância aberta. Devolve True se piorou de nível."""
        agora = datetime.now(timezone.utc)
        antes = vig.nivel
        vig.valor_atual = round(veredito["valor_atual"], 1)
        vig.taxa_por_h = veredito["taxa_por_h"]
        vig.regime = veredito["regime"]
        vig.confianca = veredito["confianca"]
        vig.dobra_h = veredito["dobra_h"]
        vig.nivel = veredito["nivel"]
        vig.ciclos = (vig.ciclos or 0) + 1
        horas = veredito["horas_ate_teto"]
        vig.estouro_em = (agora + timedelta(hours=horas)) if horas else None

        serie = list(vig.medicoes or [])
        serie.append({
            "ts": agora.isoformat(),
            "valor": vig.valor_atual,
            "taxa_por_h": vig.taxa_por_h,
            "regime": vig.regime,
        })
        vig.medicoes = serie[-LIMITE_MEDICOES:]
        return antes != "critico" and vig.nivel == "critico"

    def _rastreio_vencido(self, vig: Crescimento) -> bool:
        minutos = int(self._cfg("crescimento.rastrear_a_cada_min", 30))
        if vig.rastreado_em is None:
            return True
        quando = vig.rastreado_em
        if quando.tzinfo is None:
            quando = quando.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - quando) >= timedelta(minutes=minutos)

    async def culpar_por_serie(self, db, host, vig: Crescimento) -> bool:
        """
        Aponta o culpado de memória usando a série por container — sem
        tocar no servidor.

        Roda ANTES do rastreio por SSH de propósito: quando a resposta
        está no banco, não há por que ir buscá-la em produção. O rastreio
        continua valendo para o que a série não enxerga (processo fora de
        container, tmpfs, OOM já ocorrido no kernel).
        """
        if vig.recurso not in ("memoria", "swap"):
            return False
        try:
            culpados = await self.culpados_memoria(db, vig.host_id)
        except Exception:
            log.exception("falha ao ler a série de containers de %s", host.name)
            return False
        if not culpados:
            return False

        primeiro = culpados[0]
        vig.culpado = primeiro["nome"][:200]
        vig.causa = (primeiro.get("causa") or "")[:48]
        diagnostico = dict(vig.diagnostico or {})
        diagnostico["culpados_serie"] = culpados[:5]
        diagnostico["origem_culpado"] = "série por container (sem ida ao servidor)"
        vig.diagnostico = diagnostico
        return True

    async def rastrear_e_gravar(self, db, host, vig: Crescimento, ponto: str) -> None:
        """
        Rastreia e guarda — inclusive a comparação com o rastreio anterior,
        que é o que transforma "quem é grande" em "quem está crescendo".
        """
        try:
            resultado = await self.rastrear(host, vig.recurso, ponto)
        except Exception:
            log.exception("rastreio de crescimento falhou em %s", host.name)
            return

        serie = list(vig.medicoes or [])
        if serie:
            serie[-1] = {**serie[-1], "alvos": resultado.get("alvos") or {}}
        else:
            serie = [{
                "ts": datetime.now(timezone.utc).isoformat(),
                "valor": vig.valor_atual,
                "taxa_por_h": vig.taxa_por_h,
                "alvos": resultado.get("alvos") or {},
            }]
        vig.medicoes = serie[-LIMITE_MEDICOES:]

        crescendo = atribuir(vig.medicoes)
        resultado["crescendo"] = crescendo

        # Quem CRESCEU ganha de quem é grande. Um `data/` de 800 GB é o
        # maior do disco desde sempre e não explica nada; 40 GB a mais em
        # duas horas, sim.
        if crescendo:
            resultado["culpado"] = crescendo[0]["alvo"]
            casada = (
                casar_caminho(crescendo[0]["alvo"]) if vig.recurso == "disco"
                else casar_container(crescendo[0]["alvo"], "memoria")
            )
            resultado["causa"] = (casada or {}).get("chave", "")

        vig.culpado = (resultado.get("culpado") or "")[:200]
        vig.causa = (resultado.get("causa") or "")[:48]
        vig.diagnostico = resultado
        vig.rastreado_em = datetime.now(timezone.utc)

    def _fechar(self, host, vig: Crescimento, veredito: dict) -> dict:
        agora = datetime.now(timezone.utc)
        vig.fim = agora
        vig.valor_atual = round(veredito["valor_atual"], 1)
        if veredito["valor_atual"] >= vig.teto_pct:
            vig.desfecho = "estourou"
        elif veredito["regime"] in ("recuando", "serrote"):
            vig.desfecho = "recuou"
        else:
            vig.desfecho = "estabilizou"
        inicio = vig.inicio if vig.inicio.tzinfo else vig.inicio.replace(tzinfo=timezone.utc)
        return {
            "tipo": "retorno",
            "host_id": host.id,
            "host": host.rotulo,
            "papel": host.role,
            "servico": f"{ROTULO.get(vig.recurso, vig.recurso)} deste servidor",
            "nivel": "atencao",
            "texto": f"{ROTULO.get(vig.recurso, vig.recurso)} parou de subir "
                     f"({vig.desfecho}) — está em {vig.valor_atual}%",
            "duracao_s": (agora - inicio).total_seconds(),
            "chave": f"cre:{host.id}:{vig.recurso}:{vig.id}",
            # A abertura desta mesma vigilância. O nível pode ter subido no
            # meio (atenção -> crítico), e cada nível é uma chave própria —
            # por isso PREFIXO, e não chave exata. `desde` amarra ao
            # episódio: um aviso de duas semanas atrás, do mesmo recurso,
            # não vale como "a abertura desta".
            "chave_abertura_prefixo": f"cre:{host.id}:{vig.recurso}:",
            "abertura_desde": inicio.isoformat(),
        }

    def _evento(self, host, vig: Crescimento, tipo: str) -> dict:
        """
        O evento que vai para a tela e para o Telegram.

        A previsão entra no `texto` de propósito: é a informação que faz
        alguém agir, e ela tem de caber na primeira linha da notificação —
        não numa linha extra que o cliente de mensagem esconde.
        """
        catalogo = None
        if vig.causa:
            from app.services.catalogo_crescimento import POR_CHAVE
            catalogo = POR_CHAVE.get(vig.causa)

        rotulo = ROTULO.get(vig.recurso, vig.recurso)
        quando = ""
        if vig.estouro_em:
            alvo = vig.estouro_em
            if alvo.tzinfo is None:
                alvo = alvo.replace(tzinfo=timezone.utc)
            faltam = (alvo - datetime.now(timezone.utc)).total_seconds() / 3600
            quando = f" — chega a {vig.teto_pct:.0f}% em {_quando(max(0.0, faltam))}"

        ritmo = (
            f"dobrando a cada {_quando(vig.dobra_h)}"
            if vig.regime == "acelerando" and vig.dobra_h
            else f"subindo {vig.taxa_por_h:.2f} pontos por hora"
        )

        return {
            "tipo": tipo,
            "host_id": host.id,
            "host": host.rotulo,
            "papel": host.role,
            "servico": vig.culpado or "",
            "nivel": vig.nivel,
            "texto": f"{rotulo} em {vig.valor_atual}%, {ritmo}{quando}",
            "significa": self.dano(vig.recurso, vig.culpado, ""),
            "causa_provavel": (
                f"{vig.culpado}: {(catalogo or {}).get('por_que', '')}"
                if vig.culpado and catalogo
                else (f"o maior ocupante agora é {vig.culpado}" if vig.culpado else "")
            ),
            "acao": (catalogo or {}).get(
                "contorno",
                "Abra Crescimento no painel: a tela mostra quem está "
                "ocupando, desde quando e o que fazer.",
            ),
            "chave": f"cre:{host.id}:{vig.recurso}:{vig.nivel}",
            "duracao_s": (
                datetime.now(timezone.utc)
                - (vig.inicio if vig.inicio.tzinfo else vig.inicio.replace(tzinfo=timezone.utc))
            ).total_seconds(),
        }

    # ── Quem está com a memória: a série por container ─────────────────

    async def gravar_containers(self, db, host, containers: list[dict]) -> int:
        """
        Guarda a memória por container que a coleta **já** leu.

        Custo de servidor: zero. O `docker stats` desta passada já foi
        executado para desenhar os cartões; aqui o resultado deixa de ser
        descartado. O que se paga é linha no banco — e é por isso que a
        cadência é própria e a retenção é curta.
        """
        if not containers or not bool(self._cfg("containers.historico_ativo", True)):
            return 0

        agora = datetime.now(timezone.utc)
        minutos = max(1, int(self._cfg("containers.intervalo_min", 5)))
        ultima = self._ultima_gravacao.get(host.id)
        if ultima is not None and (agora - ultima) < timedelta(minutes=minutos):
            return 0

        # Teto por passada: instalação com centenas de containers não pode
        # virar centenas de INSERTs por gravação. Os maiores primeiro — é
        # neles que a memória da máquina está.
        teto = 60
        ordenados = sorted(
            containers, key=lambda c: c.get("memoria_bytes") or 0, reverse=True
        )[:teto]

        for c in ordenados:
            nome = str(c.get("nome") or "").strip()[:120]
            if not nome:
                continue
            db.add(AmostraContainer(
                host_id=host.id,
                ts=agora,
                nome=nome,
                mem_mb=round((c.get("memoria_bytes") or 0) / (1024 ** 2), 1),
                mem_pct=round(float(c.get("memoria_pct") or 0), 2),
                cpu_pct=round(float(c.get("cpu_pct") or 0), 2),
            ))

        self._ultima_gravacao[host.id] = agora
        return len(ordenados)

    async def serie_containers(
        self, db, host_id: int, horas: float = 6, pontos: int = 180,
        limite: int = 24, de: datetime | None = None,
        ate: datetime | None = None,
    ) -> dict:
        """
        Uma série por container, já reduzida ao que a tela desenha.

        Devolve TODOS os containers com histórico na janela (até `limite`),
        ordenados por quanto CRESCERAM — não por quanto ocupam. São
        perguntas diferentes: o `findface-tarantool-server` é o maior
        desde sempre e não explica nada; quem ganhou 900 MB em duas horas,
        sim.

        A redução acontece aqui, e não no navegador, pela mesma razão da
        série do host: mandar dez mil pontos para desenhar 240 pixels é
        tráfego jogado fora.
        """
        desde, ateh = self._intervalo(horas, de, ate)
        janela = (ateh - desde).total_seconds() / 3600

        r = await db.execute(
            select(
                AmostraContainer.ts, AmostraContainer.nome,
                AmostraContainer.mem_mb, AmostraContainer.mem_pct,
                AmostraContainer.cpu_pct,
            )
            .where(
                AmostraContainer.host_id == host_id,
                AmostraContainer.ts >= desde,
                AmostraContainer.ts <= ateh,
            )
            .order_by(AmostraContainer.ts)
        )
        linhas = list(r.all())
        # O que existe de verdade, para a tela não confundir "não há dado
        # tão para trás" com "o servidor ficou parado".
        antiga = await self._mais_antiga(db, AmostraContainer, host_id)
        contexto = {
            "de": desde.isoformat(),
            "ate": ateh.isoformat(),
            "mais_antiga": antiga.isoformat() if antiga else None,
            "retencao_dias": int(self._cfg("containers.retencao_dias", 7)),
            "intervalo_min": int(self._cfg("containers.intervalo_min", 5)),
        }
        if not linhas:
            return {
                **contexto,
                "host_id": host_id, "horas": round(janela, 2), "series": [],
                "amostras": 0,
                # Sem histórico ainda é diferente de "nenhum container
                # consome memória". A tela precisa distinguir os dois.
                "motivo": (
                    "não há gravação por container neste período — o mais "
                    f"antigo que existe é de {antiga:%d/%m %H:%M} (UTC)"
                    if antiga else
                    "ainda não há histórico por container — a primeira "
                    "gravação acontece no próximo ciclo do coletor"
                ),
            }

        por_nome: dict[str, list] = {}
        for linha in linhas:
            por_nome.setdefault(linha.nome, []).append(linha)

        series = []
        for nome, registros in por_nome.items():
            passo = max(1, len(registros) // max(30, pontos))
            escolhidos = registros[::passo]
            if escolhidos[-1] is not registros[-1]:
                escolhidos.append(registros[-1])

            valores = [(
                (reg.ts - registros[0].ts).total_seconds() / 3600, float(reg.mem_mb or 0)
            ) for reg in registros]
            inclinacao, r2 = _regressao(valores)
            memorias = [float(reg.mem_mb or 0) for reg in registros]
            cpus = [float(reg.cpu_pct or 0) for reg in registros]
            pico = max(memorias)

            series.append({
                "nome": nome,
                "atual_mb": round(float(registros[-1].mem_mb or 0), 1),
                "pico_mb": round(pico, 1),
                # Mínimo e média ao lado do pico: é o trio que separa
                # "está sempre nisso" de "subiu hoje". Um valor sozinho
                # não distingue os dois, e a diferença decide se há o que
                # investigar.
                "minimo_mb": round(min(memorias), 1),
                "media_mb": round(sum(memorias) / len(memorias), 1),
                "inicial_mb": round(memorias[0], 1),
                "variacao_mb": round(memorias[-1] - memorias[0], 1),
                "mb_por_h": round(inclinacao, 1),
                "r2": round(r2, 2),
                "mem_pct": round(float(registros[-1].mem_pct or 0), 2),
                "cpu_pct": round(cpus[-1], 2),
                "cpu_media": round(sum(cpus) / len(cpus), 2),
                "cpu_pico": round(max(cpus), 2),
                # O período que a série REALMENTE cobre. Pode ser menor que
                # a janela pedida — container criado agora tem dez minutos
                # de história, e apresentar isso como "6 horas" seria
                # dizer que a leitura vale mais do que vale.
                "de": registros[0].ts.isoformat(),
                "ate": registros[-1].ts.isoformat(),
                "amostras": len(registros),
                "pontos": [
                    {
                        "ts": reg.ts.isoformat(),
                        "mem_mb": round(float(reg.mem_mb or 0), 1),
                        "cpu_pct": round(float(reg.cpu_pct or 0), 2),
                    }
                    for reg in escolhidos
                ],
            })

        # Quem cresceu primeiro; empate desfeito por quem ocupa mais.
        series.sort(key=lambda x: (x["mb_por_h"], x["atual_mb"]), reverse=True)
        cortadas = series[:max(1, limite)]

        return {
            **contexto,
            "host_id": host_id,
            "horas": round(janela, 2),
            "amostras": len(linhas),
            "total_containers": len(series),
            "series": cortadas,
            "fora_do_teto": max(0, len(series) - len(cortadas)),
            "teto_mb": round(max(s["pico_mb"] for s in cortadas), 1),
            "em": datetime.now(timezone.utc).isoformat(),
        }

    async def culpados_memoria(
        self, db, host_id: int, horas: float | None = None,
        minimo_mb_h: float = 20.0, de: datetime | None = None,
        ate: datetime | None = None,
    ) -> list[dict]:
        """
        Quem cresceu em memória na janela — direto do banco, sem SSH.

        É a resposta que antes exigia rastreio: com a série por container
        gravada, "quem está empurrando" passa a custar uma consulta local.
        O rastreio continua valendo para o que a série não vê (processo
        fora de container, tmpfs, OOM já ocorrido).

        `minimo_mb_h` corta o ruído: container que oscila 5 MB não é
        culpado de nada.
        """
        janela = float(horas or self._cfg("crescimento.janela_h", 6))
        dados = await self.serie_containers(
            db, host_id, janela, pontos=60, limite=60, de=de, ate=ate
        )
        saida = []
        for serie in dados.get("series", []):
            if serie["mb_por_h"] < minimo_mb_h or serie["amostras"] < 3:
                continue
            catalogo = casar_container(serie["nome"], "memoria")
            saida.append({
                "nome": serie["nome"],
                "mb_por_h": serie["mb_por_h"],
                "atual_mb": serie["atual_mb"],
                "cresceu_mb": round(serie["atual_mb"] - serie["inicial_mb"], 1),
                "r2": serie["r2"],
                "causa": (catalogo or {}).get("chave", ""),
                "por_que": (catalogo or {}).get("por_que", ""),
                "contorno": (catalogo or {}).get("contorno", ""),
                "fabricante": (catalogo or {}).get("fabricante", ""),
            })
        return saida

    @staticmethod
    async def limpar_containers(db, dias: int) -> int:
        """Faxina da série por container — retenção própria, curta."""
        if dias <= 0:
            return 0
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(
            delete(AmostraContainer).where(AmostraContainer.ts < corte)
        )
        return r.rowcount or 0

    async def gravar_discos(self, db, host, discos_io: list[dict]) -> int:
        """
        Guarda IOPS/utilização por dispositivo — o `/proc/diskstats` desta
        passada já foi lido duas vezes para achar o pior (ver
        `metrics_service.calcular_io`). Custo de servidor: zero.
        """
        if not discos_io or not bool(self._cfg("discos.historico_ativo", True)):
            return 0

        agora = datetime.now(timezone.utc)
        minutos = max(1, int(self._cfg("discos.intervalo_min", 5)))
        ultima = self._ultima_gravacao_disco.get(host.id)
        if ultima is not None and (agora - ultima) < timedelta(minutes=minutos):
            return 0

        for d in discos_io:
            nome = str(d.get("dispositivo") or "").strip()[:64]
            if not nome:
                continue
            db.add(AmostraDisco(
                host_id=host.id,
                ts=agora,
                dispositivo=nome,
                iops=round(float(d.get("iops") or 0), 1),
                leitura_ps=round(float(d.get("leitura_ps") or 0), 1),
                escrita_ps=round(float(d.get("escrita_ps") or 0), 1),
                util_pct=round(float(d.get("util_pct") or 0), 1),
            ))

        self._ultima_gravacao_disco[host.id] = agora
        return len(discos_io)

    async def serie_discos(
        self, db, host_id: int, horas: float = 6, pontos: int = 240,
        de: datetime | None = None, ate: datetime | None = None,
    ) -> dict:
        """
        Uma série por dispositivo de disco, já reduzida ao que a tela
        desenha — poucos dispositivos por servidor (1 a 5, tipicamente),
        então nenhum teto de quantidade é necessário aqui, diferente da
        série de containers.
        """
        desde, ateh = self._intervalo(horas, de, ate)
        janela = (ateh - desde).total_seconds() / 3600

        r = await db.execute(
            select(
                AmostraDisco.ts, AmostraDisco.dispositivo,
                AmostraDisco.iops, AmostraDisco.util_pct,
            )
            .where(
                AmostraDisco.host_id == host_id,
                AmostraDisco.ts >= desde,
                AmostraDisco.ts <= ateh,
            )
            .order_by(AmostraDisco.ts)
        )
        linhas = list(r.all())
        antiga = await self._mais_antiga(db, AmostraDisco, host_id)
        contexto = {
            "de": desde.isoformat(),
            "ate": ateh.isoformat(),
            "mais_antiga": antiga.isoformat() if antiga else None,
            "retencao_dias": int(self._cfg("discos.retencao_dias", 7)),
            "intervalo_min": int(self._cfg("discos.intervalo_min", 5)),
        }
        if not linhas:
            return {
                **contexto,
                "host_id": host_id, "horas": round(janela, 2), "series": [],
                "motivo": (
                    "não há gravação por dispositivo neste período — a mais "
                    f"antiga que existe é de {antiga:%d/%m %H:%M} (UTC)"
                    if antiga else
                    "ainda não há histórico por dispositivo — a primeira "
                    "gravação acontece no próximo ciclo do coletor"
                ),
            }

        por_dispositivo: dict[str, list] = {}
        for linha in linhas:
            por_dispositivo.setdefault(linha.dispositivo, []).append(linha)

        series = []
        for nome, registros in por_dispositivo.items():
            passo = max(1, len(registros) // max(30, pontos))
            escolhidos = registros[::passo]
            if escolhidos[-1] is not registros[-1]:
                escolhidos.append(registros[-1])
            iops_vals = [float(reg.iops or 0) for reg in registros]
            util_vals = [float(reg.util_pct or 0) for reg in registros]
            series.append({
                "dispositivo": nome,
                "iops_media": round(sum(iops_vals) / len(iops_vals), 1),
                "iops_pico": round(max(iops_vals), 1),
                "iops_agora": round(iops_vals[-1], 1),
                "util_media": round(sum(util_vals) / len(util_vals), 1),
                "util_pico": round(max(util_vals), 1),
                "util_agora": round(util_vals[-1], 1),
                "pontos": [
                    {
                        "ts": reg.ts.isoformat(),
                        "iops": round(float(reg.iops or 0), 1),
                        "util_pct": round(float(reg.util_pct or 0), 1),
                    }
                    for reg in escolhidos
                ],
            })
        # O mais ocupado primeiro — é o candidato a olhar de perto.
        series.sort(key=lambda s: s["util_media"], reverse=True)

        return {
            **contexto,
            "host_id": host_id,
            "horas": round(janela, 2),
            "amostras": len(linhas),
            "series": series,
            "em": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    async def limpar_discos(db, dias: int) -> int:
        """Faxina da série por dispositivo de disco — retenção própria, curta."""
        if dias <= 0:
            return 0
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(
            delete(AmostraDisco).where(AmostraDisco.ts < corte)
        )
        return r.rowcount or 0

    # ── Consulta ───────────────────────────────────────────────────────

    async def analisar(
        self, db, host_id: int, horas: float | None = None,
        de: datetime | None = None, ate: datetime | None = None,
    ) -> dict:
        """
        A análise sob demanda de um servidor — só banco, sem tocar nele.

        Mostra os três recursos, inclusive os que estão estáveis: "não
        está subindo" é resposta, e é a que a tela precisa dar quando
        alguém abre por desconfiança.
        """
        desde, ateh = self._intervalo(
            horas or self._cfg("crescimento.janela_h", 6), de, ate
        )
        janela = (ateh - desde).total_seconds() / 3600
        linhas = await self._serie(db, host_id, de=desde, ate=ateh)
        ponto = (linhas[-1].disco_ponto or "") if linhas else ""

        recursos = []
        for recurso, campo in (
            ("memoria", "mem_pct"), ("disco", "disco_pct"), ("swap", "swap_pct"),
        ):
            pontos = self._pontos(linhas, campo)
            veredito = self.avaliar(recurso, pontos)
            veredito["rotulo"] = ROTULO.get(recurso, recurso)
            veredito["piso_por_h"] = round(self.piso(recurso), 3)
            veredito["dano"] = (
                self.dano(recurso, "", ponto)
                if veredito["preocupa"] or veredito["regime"] == "acelerando"
                else ""
            )
            veredito["horas_ate_teto_texto"] = _quando(veredito["horas_ate_teto"])
            # Os absolutos ao lado do percentual: "78,8%" não diz se sobra
            # 1 GB ou 40 GB, e é a sobra que decide se dá para esperar.
            if linhas:
                ultima = linhas[-1]
                if recurso == "memoria":
                    veredito["absoluto"] = (
                        f"{ultima.mem_usado_mb / 1024:.1f} GB de "
                        f"{ultima.mem_total_mb / 1024:.1f} GB"
                    )
                elif recurso == "disco":
                    veredito["absoluto"] = (
                        f"{ultima.disco_livre_gb:.0f} GB livres de "
                        f"{ultima.disco_total_gb:.0f} GB em {ponto}"
                    )
            recursos.append(veredito)

        abertas = (await db.execute(
            select(Crescimento).where(
                Crescimento.host_id == host_id, Crescimento.fim.is_(None)
            )
        )).scalars().all()

        # Quem cresceu em memória, pela série gravada. Entra sempre, e não
        # só quando há vigilância aberta: a pergunta "qual container está
        # comendo a RAM" é legítima mesmo com tudo dentro do limite.
        try:
            culpados = await self.culpados_memoria(db, host_id, janela, de=desde, ate=ateh)
        except Exception:
            log.exception("falha ao apurar culpados de memória do host %s", host_id)
            culpados = []

        antiga = await self._mais_antiga(db, Amostra, host_id)
        return {
            "host_id": host_id,
            "janela_h": round(janela, 2),
            "de": desde.isoformat(),
            "ate": ateh.isoformat(),
            "mais_antiga": antiga.isoformat() if antiga else None,
            "retencao_dias": int(self._cfg("monitor.retencao_dias", 30)),
            "amostras": len(linhas),
            "ponto": ponto,
            "recursos": recursos,
            "vigilancias": [self.como_dict(v) for v in abertas],
            "culpados_memoria": culpados,
            "lembretes": NAO_E_VAZAMENTO,
            "em": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def como_dict(vig: Crescimento, com_diagnostico: bool = False) -> dict:
        item = {
            "id": vig.id,
            "host_id": vig.host_id,
            "recurso": vig.recurso,
            "rotulo": ROTULO.get(vig.recurso, vig.recurso),
            "regime": vig.regime,
            "nivel": vig.nivel,
            "confianca": vig.confianca,
            "inicio": vig.inicio.isoformat() if vig.inicio else None,
            "fim": vig.fim.isoformat() if vig.fim else None,
            "desfecho": vig.desfecho,
            "valor_inicial": vig.valor_inicial,
            "valor_atual": vig.valor_atual,
            "taxa_por_h": vig.taxa_por_h,
            "dobra_h": vig.dobra_h,
            "teto_pct": vig.teto_pct,
            "estouro_em": vig.estouro_em.isoformat() if vig.estouro_em else None,
            "culpado": vig.culpado,
            "causa": vig.causa,
            "ciclos": vig.ciclos,
            "rastreado_em": vig.rastreado_em.isoformat() if vig.rastreado_em else None,
            "medicoes": vig.medicoes or [],
        }
        if com_diagnostico:
            item["diagnostico"] = vig.diagnostico
        return item

    async def listar(self, db, host_id: int | None = None, dias: int = 7) -> list[dict]:
        """Abertas agora, mais as fechadas na janela."""
        desde = datetime.now(timezone.utc) - timedelta(days=max(1, dias))
        consulta = select(Crescimento).where(
            (Crescimento.fim.is_(None)) | (Crescimento.fim >= desde)
        )
        if host_id is not None:
            consulta = consulta.where(Crescimento.host_id == host_id)
        r = await db.execute(consulta.order_by(Crescimento.inicio.desc()).limit(200))
        return [self.como_dict(v) for v in r.scalars().all()]

    @staticmethod
    async def limpar(db, dias: int) -> int:
        """
        Faxina: só vigilância FECHADA.

        Apagar uma aberta faria a tela achar que o problema nunca existiu
        enquanto ele ainda está acontecendo — mesma regra do incidente.
        """
        if dias <= 0:
            return 0
        corte = datetime.now(timezone.utc) - timedelta(days=dias)
        r = await db.execute(
            delete(Crescimento).where(
                Crescimento.fim.isnot(None), Crescimento.fim < corte
            )
        )
        return r.rowcount or 0
