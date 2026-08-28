"""
Configuração do FindFace que só existe em arquivo, editada pelo painel.

Parte do que decide o volume de dado gravado não está na interface nem na
API: está no arquivo de configuração do serviço legacy, e o procedimento
oficial é editar com `vi` e reiniciar os containers. Do manual da
NtechLab (settings):

    sudo vi /opt/findface-multi/configs/findface-multi-legacy/findface-multi-legacy.py
    …
    cd /opt/findface-multi/
    sudo docker-compose restart

E o aviso que manda no desenho desta tela:

    "Settings via interface/API sobrescrevem o arquivo de configuração."

Ou seja: o que dá para ajustar em **Rotatividade do FindFace** (que fala
com a API) deve ser ajustado lá, não aqui. Aqui ficam as chaves que a API
não expõe — hoje, a **programação da limpeza automática**
(`CLEANUP_SCHEDULE`, em RRULE) e o interruptor da limpeza do arquivo de
vídeo (`vms_cleanup`).

Cercas, na ordem em que agem:

1. **Lista fechada de chaves.** Não existe edição livre de arquivo. Chave
   fora da lista é recusada antes de qualquer coisa acontecer.
2. **Cópia de segurança antes de escrever**, com carimbo de data, ao lado
   do arquivo original.
3. **Validação de sintaxe depois de escrever.** O arquivo é Python; se
   `python3 -m py_compile` recusar, a cópia volta ao lugar e nada é
   reiniciado. Um arquivo quebrado aqui derruba o FindFace inteiro na
   próxima subida.
4. **O reinício é uma decisão separada.** O painel não reinicia junto: o
   manual manda reiniciar todos os containers, e isso para o
   reconhecimento. Quem escolhe a hora é quem opera.
"""
import logging
import re
import shlex

from app.services.ssh_service import SSHError, SSHService

log = logging.getLogger("faceops.configff")

SEP = "###FACEOPS:"

# Caminho oficial do manual. Confirmado em execução antes de qualquer
# escrita — instalação fora do padrão existe, e escrever num arquivo que
# não é o certo é pior que não escrever.
ARQUIVO_LEGACY = (
    "/opt/findface-multi/configs/findface-multi-legacy/findface-multi-legacy.py"
)

# As únicas chaves editáveis, e por quê.
CHAVES = {
    "CLEANUP_SCHEDULE": {
        "rotulo": "Programação da limpeza automática",
        "tipo": "rrule",
        "ajuda": (
            "Quando o FindFace roda a própria limpeza, em formato RRULE. "
            "O padrão de fábrica é RRULE:FREQ=DAILY;BYHOUR=1;BYMINUTE=17 — "
            "todo dia à 1h17. É a rotatividade nativa da plataforma: ela "
            "aplica as idades configuradas em Rotatividade do FindFace."
        ),
        "exemplo": "RRULE:FREQ=DAILY;BYHOUR=1;BYMINUTE=17",
    },
    "vms_cleanup": {
        "rotulo": "Limpeza do arquivo de vídeo",
        "tipo": "booleano",
        "ajuda": (
            "Liga a limpeza do arquivo de vídeo. Precisa estar ligada aqui "
            "para os prazos de vídeo valerem — o manual é explícito, e a "
            "limpeza de eventos não apaga gravação: 'This utility removes "
            "camera events but does not delete video archive events'."
        ),
        "exemplo": "True",
    },
}

# RRULE aceito: o suficiente para o caso do manual, sem virar um parser de
# calendário. Recusar o que não se entende é melhor que gravar algo que o
# FindFace vai ler errado às 3h da manhã.
RRULE_VALIDA = re.compile(
    r"^RRULE:FREQ=(DAILY|WEEKLY|HOURLY)"
    r"(;INTERVAL=\d{1,3})?"
    r"(;BYHOUR=\d{1,2})?"
    r"(;BYMINUTE=\d{1,2})?"
    r"(;BYDAY=(MO|TU|WE|TH|FR|SA|SU)(,(MO|TU|WE|TH|FR|SA|SU))*)?$"
)


class ConfigFFError(Exception):
    pass


def _secoes(saida: str) -> dict[str, str]:
    secoes: dict[str, str] = {}
    atual = None
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


class ConfigFFService:
    """Leitura e escrita cercada do arquivo de configuração do FindFace."""

    def __init__(self, ssh: SSHService) -> None:
        self.ssh = ssh

    # ── Leitura ────────────────────────────────────────────────────────

    async def ler(self, host) -> dict:
        """
        Estado atual das chaves editáveis. Só leitura.

        Traz também a linha exata em que cada chave aparece: é o que
        permite mostrar, antes de aplicar, o que sai e o que entra.
        """
        arquivo = shlex.quote(ARQUIVO_LEGACY)
        script = f"""
set +e
echo "{SEP}EXISTE"
[ -f {arquivo} ] && echo sim || echo nao
echo "{SEP}LINHAS"
grep -n -E "^[[:space:]]*'?(CLEANUP_SCHEDULE|vms_cleanup)'?[[:space:]]*[:=]" {arquivo} 2>/dev/null
echo "{SEP}COPIAS"
ls -1t {arquivo}.faceops-* 2>/dev/null | head -5
echo "{SEP}FIM"
"""
        try:
            r = await self.ssh.run(host, script, sudo=True, timeout=60)
        except SSHError as exc:
            raise ConfigFFError(f"não consegui ler a configuração: {exc}") from exc

        s = _secoes(r.stdout or "")
        if s.get("EXISTE", "").strip() != "sim":
            raise ConfigFFError(
                f"não encontrei {ARQUIVO_LEGACY} em '{host.name}'. Se a "
                "instalação está em outro caminho, a edição por aqui fica "
                "indisponível — e é melhor assim do que escrever no lugar errado."
            )

        atuais: dict[str, dict] = {}
        for linha in (s.get("LINHAS") or "").splitlines():
            if ":" not in linha:
                continue
            numero, _, conteudo = linha.partition(":")
            for chave in CHAVES:
                if re.search(rf"'?{chave}'?\s*[:=]", conteudo):
                    valor = conteudo.split(":", 1)[-1] if ":" in conteudo else conteudo
                    atuais[chave] = {
                        "linha": numero.strip(),
                        "conteudo": conteudo.strip(),
                        "valor": _valor_da_linha(conteudo),
                    }

        campos = []
        for chave, meta in CHAVES.items():
            achado = atuais.get(chave)
            campos.append({
                "chave": chave,
                **meta,
                "presente": achado is not None,
                "valor": achado["valor"] if achado else "",
                "linha": achado["linha"] if achado else "",
                "conteudo": achado["conteudo"] if achado else "",
            })

        return {
            "arquivo": ARQUIVO_LEGACY,
            "campos": campos,
            "copias": [c for c in (s.get("COPIAS") or "").splitlines() if c.strip()],
        }

    # ── Escrita ────────────────────────────────────────────────────────

    def validar(self, chave: str, valor: str) -> str:
        """Valida antes de tocar no servidor. Devolve o valor normalizado."""
        if chave not in CHAVES:
            raise ConfigFFError(f"chave não editável: {chave!r}")

        tipo = CHAVES[chave]["tipo"]
        valor = (valor or "").strip()

        if tipo == "rrule":
            valor = valor.upper().replace(" ", "")
            if not RRULE_VALIDA.match(valor):
                raise ConfigFFError(
                    f"RRULE não reconhecida: {valor!r}. Formato aceito, como no "
                    "manual: RRULE:FREQ=DAILY;BYHOUR=1;BYMINUTE=17"
                )
            return valor

        if tipo == "booleano":
            baixo = valor.lower()
            if baixo in ("true", "sim", "1", "on"):
                return "True"
            if baixo in ("false", "nao", "não", "0", "off"):
                return "False"
            raise ConfigFFError(f"valor booleano inválido: {valor!r}")

        raise ConfigFFError(f"tipo desconhecido para {chave}")

    async def aplicar(self, host, chave: str, valor: str, simular: bool = True) -> dict:
        """
        Grava uma chave. Com `simular`, só mostra o que mudaria.

        Sequência, e a razão de cada passo:

        1. Confere que a chave existe no arquivo — criar chave nova em
           arquivo de fabricante é convite a duplicidade silenciosa.
        2. Copia o arquivo com carimbo de data.
        3. Substitui **só aquela linha**, preservando indentação e o resto.
        4. Compila o arquivo. Se não compilar, restaura a cópia e para.
        5. Não reinicia nada: o manual manda reiniciar todos os
           containers, e isso é decisão de quem opera, na hora que ele
           escolher.
        """
        valor = self.validar(chave, valor)
        estado = await self.ler(host)
        campo = next((c for c in estado["campos"] if c["chave"] == chave), None)
        if campo is None or not campo["presente"]:
            raise ConfigFFError(
                f"'{chave}' não está no arquivo desta instalação. O painel não "
                "cria chave nova em arquivo de fabricante — se ela deveria "
                "existir, trate com o suporte da NtechLab."
            )

        linha_antiga = campo["conteudo"]
        linha_nova = _trocar_valor(linha_antiga, chave, valor)
        if linha_nova == linha_antiga:
            return {
                "ok": True,
                "mudou": False,
                "antes": linha_antiga,
                "depois": linha_nova,
                "mensagem": "o valor já é esse — nada a fazer",
            }

        if simular:
            return {
                "ok": True,
                "mudou": True,
                "simulado": True,
                "arquivo": ARQUIVO_LEGACY,
                "linha": campo["linha"],
                "antes": linha_antiga,
                "depois": linha_nova,
            }

        arquivo = shlex.quote(ARQUIVO_LEGACY)
        numero = str(int(campo["linha"]))  # int() = validação, não vem do usuário
        nova = shlex.quote(linha_nova)

        script = f"""
set -e
COPIA={arquivo}.faceops-$(date +%Y%m%d-%H%M%S)
cp -a {arquivo} "$COPIA"
echo "{SEP}COPIA"
echo "$COPIA"
# Rotatividade: mantem as 5 copias mais recentes. Copia de seguranca que
# ninguem apaga vira lixo no diretorio de configuracao do fabricante.
ls -1t {arquivo}.faceops-* 2>/dev/null | tail -n +6 | xargs -r rm -f

python3 - "$COPIA" <<'PY'
import sys
copia = sys.argv[1]
alvo = {ARQUIVO_LEGACY!r}
numero = {numero}
nova = {linha_nova!r}
with open(alvo, encoding='utf-8') as f:
    linhas = f.readlines()
if numero < 1 or numero > len(linhas):
    raise SystemExit('linha fora do arquivo')
antiga = linhas[numero - 1]
recuo = antiga[:len(antiga) - len(antiga.lstrip())]
linhas[numero - 1] = recuo + nova.lstrip() + ('\\n' if not nova.endswith('\\n') else '')
with open(alvo, 'w', encoding='utf-8') as f:
    f.writelines(linhas)
PY

echo "{SEP}COMPILA"
if python3 -m py_compile {arquivo} 2>&1; then
  echo OK
else
  cp -a "$COPIA" {arquivo}
  echo RESTAURADO
fi
echo "{SEP}DEPOIS"
sed -n "{numero}p" {arquivo}
echo "{SEP}FIM"
"""
        # `nova` entra citada no shell só para o log; o valor real vai pelo
        # heredoc em Python, onde não há interpretação de shell.
        _ = nova

        try:
            r = await self.ssh.run(host, script, sudo=True, timeout=120)
        except SSHError as exc:
            raise ConfigFFError(f"a gravação falhou em '{host.name}': {exc}") from exc

        s = _secoes(r.stdout or "")
        compila = (s.get("COMPILA") or "").strip()
        if "RESTAURADO" in compila or not r.ok:
            raise ConfigFFError(
                "o arquivo não compilou depois da alteração e foi restaurado "
                f"da cópia. Nada foi reiniciado. Saída: {compila[:400]}"
            )

        return {
            "ok": True,
            "mudou": True,
            "simulado": False,
            "arquivo": ARQUIVO_LEGACY,
            "copia": (s.get("COPIA") or "").strip(),
            "antes": linha_antiga,
            "depois": (s.get("DEPOIS") or "").strip(),
            "aviso_reinicio": (
                "O FindFace só passa a valer isto depois de reiniciar os "
                "containers — o manual manda `docker compose restart` no "
                "diretório da instalação. Isso PARA o reconhecimento por "
                "alguns minutos; escolha a hora."
            ),
        }


def _valor_da_linha(conteudo: str) -> str:
    """Extrai o valor de `'CHAVE': valor,` ou `CHAVE = valor`."""
    corpo = conteudo.split(":", 1)[-1] if ":" in conteudo else conteudo
    if "=" in corpo and ":" not in conteudo:
        corpo = corpo.split("=", 1)[-1]
    return corpo.strip().rstrip(",").strip().strip("'\"")


def _trocar_valor(linha: str, chave: str, valor: str) -> str:
    """
    Reescreve a linha mantendo o formato que já estava lá.

    Preserva aspas, vírgula final e o estilo (dicionário `'X': v,` ou
    atribuição `X = v`) — arquivo de fabricante não é lugar para o painel
    impor estilo próprio.
    """
    aspas = "'" if f"'{chave}'" in linha else ""
    virgula = "," if linha.rstrip().endswith(",") else ""
    booleano = valor in ("True", "False")
    valor_escrito = valor if booleano else f"'{valor}'"

    if ":" in linha and (f"'{chave}'" in linha or f'"{chave}"' in linha):
        return f"{aspas or chr(39)}{chave}{aspas or chr(39)}: {valor_escrito}{virgula}"
    return f"{chave} = {valor_escrito}{virgula}"
