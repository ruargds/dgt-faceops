"""
Manutenção de disco e log dos servidores do FindFace.

Existe porque o problema mais comum em servidor de reconhecimento facial
não é o FindFace: é o disco raiz enchendo de log. Num servidor real
encontramos 99 GB de `/var/log` num disco de 123 GB, gerados pelo log de
acesso HTTP em operação NORMAL — cerca de 8 GB por dia.

Três operações, todas pela web e nenhuma reiniciando o FindFace:

* **Diagnóstico** — o que está enchendo, a que velocidade, e o que já
  está configurado. Só leitura.
* **Contenção** — filtra o ruído na chegada ao rsyslog, limita o journald
  e rotaciona o syslog por tamanho. Reinicia apenas o rsyslog, que é
  instantâneo e não toca em container.
* **Arquivamento** — move log rotacionado para um disco com folga e
  comprime lá. Não apaga nada.

Toda ação tem modo simulação, e o que ela escreveria é devolvido para a
tela antes de qualquer alteração.
"""
import re
import shlex

from app.services.ssh_service import CommandResult, SSHError, SSHService

SEP = "###FACEOPS:"

# Janela da medição de crescimento. Curta o bastante para caber numa
# requisição HTTP, longa o bastante para um log movimentado dar sinal.
JANELA_S = 15

# ── Conteúdo que a contenção escreve ───────────────────────────────────

CONF_RSYSLOG = r"""# FaceOps — reduz log de container no /var/log/syslog.
#
# Descarta APENAS requisicao HTTP bem-sucedida. Erro, aviso e qualquer
# status fora de 2xx/3xx continuam sendo gravados. O log completo segue
# acessivel por `docker logs` e `journalctl` — aqui so evitamos gravar o
# ruido no disco raiz.
#
# Nada do FindFace reinicia: o filtro age na chegada ao rsyslog.
if re_match($programname, "^[0-9a-f]{12}$") then {
    if re_match($msg, "status=(200|204|206|304)") then stop
    if re_match($msg, "HTTP/1\.[01]\" (200|204|206|304) ") then stop
    if re_match($msg, "HTTP RESP .* (200|204|206|304) \[") then stop
}
"""

CONF_JOURNALD = """# FaceOps — teto do journal.
# Sem isto o journald cresce ate 10% do disco; com dezenas de containers
# logando, passa muito disso antes de alguem perceber.
[Journal]
SystemMaxUse=2G
SystemMaxFileSize=200M
MaxRetentionSec=2week
"""

CONF_LOGROTATE = """# FaceOps — rotaciona o syslog por TAMANHO, nao so por data.
# A rotacao padrao do Ubuntu e semanal; a 8 GB/dia isso nao segura.
/var/log/syslog
{
    rotate 7
    daily
    maxsize 500M
    missingok
    notifempty
    compress
    delaycompress
    sharedscripts
    postrotate
        /usr/lib/rsyslog/rsyslog-rotate 2>/dev/null || true
    endscript
}
"""

ARQUIVOS = {
    "rsyslog": "/etc/rsyslog.d/30-faceops-docker.conf",
    "journald": "/etc/systemd/journald.conf.d/faceops-limite.conf",
    "logrotate": "/etc/logrotate.d/faceops-syslog",
}


class ManutencaoError(Exception):
    pass


def _split(saida: str) -> dict[str, str]:
    secoes: dict[str, str] = {}
    atual: str | None = None
    buf: list[str] = []
    for linha in saida.splitlines():
        if linha.startswith(SEP):
            if atual is not None:
                secoes[atual] = "\n".join(buf)
            atual = linha[len(SEP):].strip()
            buf = []
        elif atual is not None:
            buf.append(linha)
    if atual is not None:
        secoes[atual] = "\n".join(buf)
    return secoes


def _parse_df(texto: str) -> list[dict]:
    saida: list[dict] = []
    for linha in texto.strip().splitlines()[1:]:
        p = linha.split()
        if len(p) < 6:
            continue
        try:
            total, usado, livre = int(p[1]), int(p[2]), int(p[3])
        except ValueError:
            continue
        saida.append({
            "dispositivo": p[0],
            "ponto": " ".join(p[5:]),
            "total_bytes": total,
            "usado_bytes": usado,
            "livre_bytes": livre,
            "percentual": round(usado / total * 100, 1) if total else 0.0,
        })
    return saida


class MaintenanceService:
    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh

    # ── Diagnóstico (somente leitura) ──────────────────────────────────

    async def diagnostico(self, host) -> dict:
        """
        Levanta o estado de disco e log. Não altera nada.

        A medição de crescimento é feita amostrando o tamanho do syslog
        duas vezes com 15s de intervalo. É o único número que diz se a
        contenção vale a pena — e depois, se funcionou.
        """
        script = f"""
set +e
echo "{SEP}DF"
df -B1 -P -x tmpfs -x devtmpfs -x overlay -x squashfs 2>/dev/null

echo "{SEP}VARLOG_TOTAL"
du -sb /var/log 2>/dev/null | cut -f1

echo "{SEP}ARQUIVOS"
find /var/log -xdev -maxdepth 1 -type f -printf '%s\\t%p\\n' 2>/dev/null | sort -rn | head -12

echo "{SEP}JOURNAL"
journalctl --disk-usage 2>/dev/null

echo "{SEP}CRESCIMENTO"
A=$(stat -c %s /var/log/syslog 2>/dev/null || echo 0)
sleep {JANELA_S}
B=$(stat -c %s /var/log/syslog 2>/dev/null || echo 0)
echo "$A $B {JANELA_S}"

echo "{SEP}CONTAINER_NO_SYSLOG"
tail -n 2000 /var/log/syslog 2>/dev/null | grep -cE ' [0-9a-f]{{12}}\\[[0-9]+\\]:'

echo "{SEP}AMOSTRA"
tail -n 3 /var/log/syslog 2>/dev/null

echo "{SEP}JA_CONFIGURADO"
for f in {ARQUIVOS["rsyslog"]} {ARQUIVOS["journald"]} {ARQUIVOS["logrotate"]}; do
  [ -f "$f" ] && echo "sim $f" || echo "nao $f"
done

echo "{SEP}DRIVER"
docker info --format '{{{{.LoggingDriver}}}}' 2>/dev/null

echo "{SEP}END"
"""
        # sudo porque o /var/log costuma ser 0640 root:adm
        r: CommandResult = await self.ssh.run_script(
            host, script, sudo=True, timeout=JANELA_S + 180
        )
        if not r.stdout.strip():
            raise SSHError(f"diagnostico vazio em '{host.name}': {r.stderr[:300]}")

        s = _split(r.stdout)

        # Crescimento
        cresc_dia = 0
        try:
            a, b, janela = (int(x) for x in s.get("CRESCIMENTO", "0 0 1").split())
            if b >= a and janela > 0:
                cresc_dia = int((b - a) * 86400 / janela)
        except (ValueError, TypeError):
            pass

        arquivos = []
        for linha in s.get("ARQUIVOS", "").strip().splitlines():
            partes = linha.split("\t", 1)
            if len(partes) != 2:
                continue
            try:
                arquivos.append({"bytes": int(partes[0]), "caminho": partes[1]})
            except ValueError:
                continue

        configurado = {}
        for linha in s.get("JA_CONFIGURADO", "").strip().splitlines():
            partes = linha.split(None, 1)
            if len(partes) == 2:
                for chave, caminho in ARQUIVOS.items():
                    if partes[1] == caminho:
                        configurado[chave] = partes[0] == "sim"

        try:
            varlog_total = int(s.get("VARLOG_TOTAL", "0").strip() or 0)
        except ValueError:
            varlog_total = 0

        try:
            linhas_container = int(s.get("CONTAINER_NO_SYSLOG", "0").strip() or 0)
        except ValueError:
            linhas_container = 0

        discos = _parse_df(s.get("DF", ""))

        # Sugere o destino de arquivamento: a montagem com mais espaço
        # livre que não seja a raiz.
        candidatos = [d for d in discos if d["ponto"] != "/" and d["livre_bytes"] > 0]
        candidatos.sort(key=lambda d: d["livre_bytes"], reverse=True)
        destino = (
            f"{candidatos[0]['ponto'].rstrip('/')}/logs-arquivados"
            if candidatos else ""
        )

        # Só vale arquivar o que está rotacionado — o ativo é tratado à parte
        rotacionados = [
            a for a in arquivos
            if re.search(r"/syslog\.\d+(\.gz)?$|\.\d+\.gz$", a["caminho"])
        ]

        return {
            "host_id": host.id,
            "host": host.name,
            "discos": discos,
            "discos_criticos": [d for d in discos if d["percentual"] >= 90],
            "varlog_bytes": varlog_total,
            "arquivos": arquivos,
            "rotacionados": rotacionados,
            "rotacionados_bytes": sum(a["bytes"] for a in rotacionados),
            "journal": s.get("JOURNAL", "").strip(),
            "crescimento_bytes_dia": cresc_dia,
            "linhas_container_no_syslog": linhas_container,
            "container_polui_syslog": linhas_container > 0,
            "amostra": s.get("AMOSTRA", "").strip()[:2000],
            "log_driver": s.get("DRIVER", "").strip(),
            "contencao_aplicada": configurado,
            "destino_sugerido": destino,
        }

    # ── Contenção ──────────────────────────────────────────────────────

    async def aplicar_contencao(self, host, *, simular: bool = True) -> dict:
        """
        Aplica o filtro de log. Com `simular=True` devolve o que faria.

        O rsyslog é validado com `rsyslogd -N1` ANTES de reiniciar. Uma
        configuração inválida derrubaria o log do servidor inteiro — e
        o rsyslog não volta sozinho.
        """
        previsto = [
            {"caminho": ARQUIVOS["rsyslog"], "conteudo": CONF_RSYSLOG,
             "efeito": "Descarta requisição HTTP bem-sucedida de container. "
                       "Erros continuam. Reinicia só o rsyslog."},
            {"caminho": ARQUIVOS["journald"], "conteudo": CONF_JOURNALD,
             "efeito": "Limita o journal a 2 GB. Reinicia só o journald."},
            {"caminho": ARQUIVOS["logrotate"], "conteudo": CONF_LOGROTATE,
             "efeito": "Rotaciona o syslog ao passar de 500 MB, não só por data."},
        ]

        if simular:
            return {"simulado": True, "alteracoes": previsto, "log": "",
                    "aplicado": False}

        # Heredoc com delimitador aleatório evita colisão com o conteúdo
        def bloco(caminho: str, conteudo: str) -> str:
            return (
                f"mkdir -p $(dirname {shlex.quote(caminho)})\n"
                f"if [ -f {shlex.quote(caminho)} ]; then\n"
                f"  cp -a {shlex.quote(caminho)} {shlex.quote(caminho)}.faceops-bak-$(date +%s)\n"
                f"fi\n"
                f"cat > {shlex.quote(caminho)} <<'FACEOPSCONF'\n"
                f"{conteudo}"
                f"FACEOPSCONF\n"
                f"echo 'escrito: {caminho}'\n"
            )

        script = f"""
set -e
{bloco(ARQUIVOS["journald"], CONF_JOURNALD)}
{bloco(ARQUIVOS["logrotate"], CONF_LOGROTATE)}
{bloco(ARQUIVOS["rsyslog"], CONF_RSYSLOG)}

echo "{SEP}VALIDACAO"
# Validar ANTES de reiniciar. Config invalida derruba o log do servidor.
if rsyslogd -N1 2>&1; then
  echo "rsyslog-config: OK"
else
  echo "rsyslog-config: INVALIDA"
  rm -f {shlex.quote(ARQUIVOS["rsyslog"])}
  echo "filtro removido — nada foi reiniciado"
  exit 3
fi

echo "{SEP}REINICIO"
systemctl restart systemd-journald && echo "journald reiniciado"
systemctl restart rsyslog && echo "rsyslog reiniciado"

echo "{SEP}LOGROTATE"
logrotate -d {shlex.quote(ARQUIVOS["logrotate"])} 2>&1 | tail -6

echo "{SEP}END"
"""
        r = await self.ssh.run_script(host, script, sudo=True, timeout=180)

        if r.exit_status == 3:
            raise ManutencaoError(
                "a configuração do rsyslog não passou na validação. O filtro foi "
                "removido e nada foi reiniciado — o servidor está como estava.\n\n"
                + r.stdout[-1500:]
            )
        if not r.ok:
            raise ManutencaoError(
                f"falha ao aplicar em '{host.name}':\n{(r.stderr or r.stdout)[-1500:]}"
            )

        return {"simulado": False, "aplicado": True, "alteracoes": previsto,
                "log": r.stdout[-6000:]}

    # ── Arquivamento ───────────────────────────────────────────────────

    async def arquivar_logs(
        self, host, destino: str, *, simular: bool = True, incluir_ativo: bool = False
    ) -> dict:
        """
        Move log rotacionado para um disco com folga e comprime lá.

        **Nada é apagado.** O rotacionado é movido (ninguém o tem aberto);
        o `syslog` ativo, se incluído, é COPIADO e depois zerado com
        `truncate` — nunca `rm`, porque o rsyslog o mantém aberto e apagar
        libera o nome sem devolver o espaço.
        """
        if not destino or not destino.startswith("/") or ".." in destino:
            raise ManutencaoError(f"destino inválido: {destino!r}")

        d = shlex.quote(destino)
        acao_ativo = ""
        if incluir_ativo:
            acao_ativo = f"""
echo "{SEP}ATIVO"
if [ -s /var/log/syslog ]; then
  ALVO={d}/syslog-$(date +%F_%H%M%S)
  cp /var/log/syslog "$ALVO" && echo "copiado para $ALVO"
  # truncate, nunca rm: o rsyslog mantem o arquivo aberto
  truncate -s 0 /var/log/syslog && echo "syslog zerado (conteudo preservado na copia)"
fi
"""

        script = f"""
set +e
echo "{SEP}ANTES"
df -B1 -P / | tail -1

echo "{SEP}DESTINO"
{"echo 'SIMULACAO — nada sera criado'" if simular else f"mkdir -p {d} && echo 'destino pronto: {destino}'"}
df -B1 -P {d} 2>/dev/null | tail -1

echo "{SEP}CANDIDATOS"
find /var/log -xdev -maxdepth 1 -type f \\( -name 'syslog.*' -o -name '*.log.[0-9]*' \\) \\
  -printf '%s\\t%p\\n' 2>/dev/null | sort -rn

{"" if simular else f'''
echo "{SEP}MOVENDO"
for f in $(find /var/log -xdev -maxdepth 1 -type f \\( -name 'syslog.*' -o -name '*.log.[0-9]*' \\) 2>/dev/null); do
  # mv entre discos escreve no destino antes de liberar a origem —
  # funciona mesmo com o disco de origem sem espaco nenhum
  mv "$f" {d}/ && echo "movido: $f"
done
{acao_ativo}
echo "{SEP}COMPRIMINDO"
# em segundo plano: comprimir dezenas de GB demora, e o espaco ja foi
# liberado no passo anterior
nohup sh -c 'gzip -f {destino}/syslog-* {destino}/syslog.[0-9] 2>/dev/null' >/dev/null 2>&1 &
echo "compressao iniciada em segundo plano"

echo "{SEP}DEPOIS"
df -B1 -P / | tail -1
'''}
echo "{SEP}END"
"""
        r = await self.ssh.run_script(host, script, sudo=True, timeout=600)
        s = _split(r.stdout)

        candidatos = []
        for linha in s.get("CANDIDATOS", "").strip().splitlines():
            partes = linha.split("\t", 1)
            if len(partes) == 2:
                try:
                    candidatos.append({"bytes": int(partes[0]), "caminho": partes[1]})
                except ValueError:
                    continue

        def _livre(secao: str) -> int:
            linha = s.get(secao, "").strip().splitlines()
            if not linha:
                return 0
            p = linha[-1].split()
            try:
                return int(p[3])
            except (ValueError, IndexError):
                return 0

        return {
            "simulado": simular,
            "destino": destino,
            "candidatos": candidatos,
            "total_bytes": sum(c["bytes"] for c in candidatos),
            "livre_antes": _livre("ANTES"),
            "livre_depois": _livre("DEPOIS") if not simular else 0,
            "log": r.stdout[-6000:],
            "ok": r.ok,
        }
