"""
InTerminal — terminal SSH pelo navegador.

Ponte entre o WebSocket do navegador (xterm.js) e um PTY real no servidor,
via asyncssh. Não é emulação: é o shell de verdade, com cores, `top`,
`htop`, editor, tudo.

Duas garantias que um terminal web precisa ter para ser aceitável em
produção:

* **Gravação.** Toda sessão é gravada em asciicast v2. Se alguém rodar
  `rm -rf` num servidor de reconhecimento facial, existe o registro de
  quem, quando e o quê.
* **Sessão própria por conexão.** Nenhum reaproveitamento da conexão do
  pool — um PTY interativo não pode dividir canal com coleta de métrica.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncssh

from app.core.config import settings
from app.core.vault import decrypt_secret, encrypt_secret
from app.services import erros_conexao
from app.services.ssh_service import SSHError

log = logging.getLogger("faceops.terminal")

# Teto de entrada por mensagem — colar 50 MB no terminal não pode virar
# consumo de memória do painel.
MAX_ENTRADA = 64 * 1024


class Gravador:
    """
    Grava a sessão em asciicast v2 (formato do asciinema), **cifrado**.

    O formato foi escolhido porque é uma linha JSON por evento: dá para
    reproduzir com `asciinema play` e para achar um comando específico
    numa auditoria.

    ## Por que cifrar, e por que linha a linha

    A gravação é o artefato mais sensível do painel depois das
    credenciais: ela contém TUDO que foi digitado, inclusive a senha de
    sudo quando o operador a digita num prompt, e o conteúdo de qualquer
    arquivo que ele tenha aberto. Em claro no disco, ela vale mais para
    um invasor do que o próprio cofre.

    Cifrar só ao fechar deixaria a sessão inteira em claro enquanto ela
    dura — e uma sessão de plantão dura horas. Pior: painel derrubado no
    meio deixaria o arquivo em claro para sempre. Cifrando LINHA A LINHA,
    o texto claro nunca toca o disco.

    A chave é a mesma do cofre (`core.vault`, derivada da SECRET_KEY), e
    isso tem uma consequência que precisa estar dita: **perder a
    SECRET_KEY torna as gravações ilegíveis**. É o mesmo trato já aceito
    para as credenciais, e é a razão de a SECRET_KEY ficar deliberadamente
    fora do backup do painel (docs/19).

    Arquivo novo sai com sufixo `.cast.enc`. O `.cast` em claro continua
    sendo lido na hora de baixar — gravação antiga não deixa de abrir por
    causa desta mudança.
    """

    def __init__(
        self, caminho: Path, colunas: int, linhas: int, titulo: str,
        cifrar: bool = True,
    ) -> None:
        self.cifrar = cifrar
        # O sufixo é o que diz ao leitor se precisa decifrar. Sem ele, a
        # única forma de saber seria tentar e ver se falha.
        if cifrar and caminho.suffix != ".enc":
            caminho = caminho.with_suffix(caminho.suffix + ".enc")
        self.caminho = caminho
        self.inicio = time.time()
        self._fh = None
        self._lock = asyncio.Lock()
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self._fh = caminho.open("w", encoding="utf-8", buffering=1)
        cabecalho = {
            "version": 2,
            "width": colunas,
            "height": linhas,
            "timestamp": int(self.inicio),
            "title": titulo,
            "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"},
        }
        self._escrever_linha(json.dumps(cabecalho, ensure_ascii=False))

    def _escrever_linha(self, linha: str) -> None:
        """Uma linha do asciicast, cifrada quando for o caso."""
        if self.cifrar:
            # Fernet devolve base64 urlsafe: não contém '\n', então a
            # quebra de linha continua sendo separador confiável.
            linha = encrypt_secret(linha)
        self._fh.write(linha + "\n")

    async def escrever(self, dados: str, canal: str = "o") -> None:
        if self._fh is None:
            return
        async with self._lock:
            try:
                evento = [round(time.time() - self.inicio, 6), canal, dados]
                self._escrever_linha(json.dumps(evento, ensure_ascii=False))
            except (OSError, ValueError):
                pass

    def fechar(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None


def ler_gravacao(caminho: Path) -> str:
    """
    O asciicast em claro, para baixar e reproduzir.

    Aceita os dois formatos, e é essa a razão de existir: gravação feita
    antes da cifragem (`.cast`) continua abrindo normalmente, e o
    operador não descobre que perdeu o histórico justamente no dia em
    que precisa dele.

    Linha ilegível não derruba o arquivo inteiro: entra como um evento de
    comentário e a reprodução segue. Uma gravação truncada por queda do
    painel ainda vale pelo que tem — perder as 3 mil linhas boas por
    causa da última, escrita pela metade, seria trocar um problema
    pequeno por um grande.
    """
    bruto = caminho.read_text(encoding="utf-8", errors="replace")
    if caminho.suffix != ".enc":
        return bruto

    linhas = []
    for linha in bruto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        try:
            linhas.append(decrypt_secret(linha))
        except ValueError:
            linhas.append(json.dumps(
                [0.0, "o", "[linha ilegível: gravada com outra SECRET_KEY]\r\n"]
            ))
    return "\n".join(linhas) + "\n"


class SessaoTerminal:
    """Uma sessão viva. Vive enquanto o WebSocket estiver aberto."""

    def __init__(
        self,
        host,
        usuario: str,
        ip: str,
        permite_sudo: bool,
        ssh_usuario: str = "",
        ssh_senha: str = "",
    ) -> None:
        self.host = host
        self.usuario = usuario
        self.ip = ip
        self.permite_sudo = permite_sudo

        # Credencial digitada na tela, válida só para esta sessão. Fica em
        # memória, é descartada no instante em que o SSH autentica e não
        # entra no banco, na auditoria nem na gravação da sessão.
        self.login = (ssh_usuario or host.ssh_user).strip() or host.ssh_user
        self.credencial_propria = bool(ssh_senha)
        self._senha_sessao = ssh_senha

        self.conn: asyncssh.SSHClientConnection | None = None
        self.processo: asyncssh.SSHClientProcess | None = None
        self.gravador: Gravador | None = None

        self.bytes_in = 0
        self.bytes_out = 0
        self.sudo_usado = False
        self.ultima_atividade = time.monotonic()
        self.motivo_fim = ""

    # ── Ciclo de vida ──────────────────────────────────────────────────

    async def abrir(self, colunas: int = 120, linhas: int = 32) -> None:
        from app.services.ssh_service import SSHService

        # Conexão dedicada, fora do pool: o PTY fica ocupado a sessão
        # inteira e não pode disputar canal com a coleta de métricas.
        opcoes = SSHService._build_options(
            self.host, credencial=(self.login, self._senha_sessao)
        )
        try:
            self.conn = await asyncssh.connect(self.host.address, **opcoes)
        except asyncssh.PermissionDenied as exc:
            # A mensagem diz COM QUE credencial a tentativa foi feita. Sem
            # isso, "login recusado" manda conferir senha quando a sessao
            # estava usando a chave PEM do cofre, e ninguem adivinha.
            if self.credencial_propria:
                origem = "a senha digitada nesta sessao"
            elif getattr(self.host, "auth_method", "") == "key":
                origem = "a chave PEM cadastrada do servidor"
            else:
                origem = "a senha cadastrada do servidor"
            raise SSHError(
                f"o servidor '{self.host.name}' recusou o login de "
                f"'{self.login}' usando {origem}: {exc}"
            ) from exc
        except asyncssh.HostKeyNotVerifiable as exc:
            raise SSHError(
                f"a chave de '{self.host.name}' nao confere com a cadastrada — "
                "sessao recusada."
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            # Traduzido: "[Errno 111] Connection refused" está correto e
            # não ajuda ninguém às 3h. Os três erros mais comuns têm
            # causas OPOSTAS e a mesma cara — e mandar conferir a rede
            # quando o problema é o sshd parado custa o dobro do tempo.
            info = erros_conexao.explicar(exc)
            partes = [
                f"não consegui abrir sessão em '{self.host.rotulo}': "
                f"{info['resumo'].lower()}"
            ]
            if info["significa"]:
                partes.append(info["significa"])
            if info["acao"]:
                partes.append(info["acao"])
            partes.append(f"(erro do sistema: {info['erro']})")
            raise SSHError(" ".join(partes)) from exc
        finally:
            # Autenticou ou não, a senha digitada não tem mais uso. Some da
            # memória do painel antes de qualquer outra coisa acontecer.
            self._senha_sessao = ""

        try:
            self.processo = await self.conn.create_process(
                term_type="xterm-256color",
                term_size=(colunas, linhas),
                encoding="utf-8",
                errors="replace",
                stderr=asyncssh.STDOUT,
            )
        except (OSError, asyncssh.Error) as exc:
            await self.fechar("erro ao alocar PTY")
            raise SSHError(f"falha ao abrir PTY em '{self.host.name}': {exc}") from exc

        if settings.TERMINAL_RECORD:
            carimbo = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            nome = f"{carimbo}_{self.host.name}_{self.usuario}.cast"
            self.gravador = Gravador(
                Path(settings.TERMINAL_SESSION_DIR) / nome,
                colunas,
                linhas,
                f"{self.usuario}@{self.host.name}",
            )

    async def fechar(self, motivo: str = "encerrada") -> None:
        self.motivo_fim = motivo
        if self.processo is not None:
            try:
                self.processo.close()
            except Exception:
                pass
            self.processo = None
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        if self.gravador is not None:
            self.gravador.fechar()

    @property
    def caminho_gravacao(self) -> str:
        return str(self.gravador.caminho) if self.gravador else ""

    # ── Fluxo de dados ─────────────────────────────────────────────────

    async def escrever(self, dados: str) -> None:
        """Entrada do navegador para o shell."""
        if self.processo is None:
            return
        if len(dados) > MAX_ENTRADA:
            dados = dados[:MAX_ENTRADA]

        # Sinaliza uso de sudo para a auditoria. Não é filtro de segurança
        # (basta um alias para escapar) — é rastro para quem lê o log.
        if "sudo" in dados:
            self.sudo_usado = True

        self.bytes_in += len(dados)
        self.ultima_atividade = time.monotonic()

        if self.gravador is not None:
            await self.gravador.escrever(dados, "i")

        try:
            self.processo.stdin.write(dados)
        except (BrokenPipeError, ConnectionResetError, asyncssh.Error):
            await self.fechar("conexao com o servidor caiu")

    async def ler(self) -> str | None:
        """Saída do shell para o navegador. None quando o shell terminou."""
        if self.processo is None:
            return None
        try:
            dados = await self.processo.stdout.read(65536)
        except (asyncssh.Error, OSError):
            return None
        if not dados:
            return None

        self.bytes_out += len(dados)
        if self.gravador is not None:
            await self.gravador.escrever(dados, "o")
        return dados

    async def redimensionar(self, colunas: int, linhas: int) -> None:
        """Acompanha o tamanho da janela do navegador."""
        if self.processo is None:
            return
        colunas = max(20, min(int(colunas), 500))
        linhas = max(5, min(int(linhas), 200))
        try:
            self.processo.change_terminal_size(colunas, linhas)
        except (asyncssh.Error, OSError):
            pass

    @property
    def ocioso_ha(self) -> float:
        return time.monotonic() - self.ultima_atividade

    @property
    def expirou(self) -> bool:
        return self.ocioso_ha > settings.TERMINAL_IDLE_TIMEOUT_MIN * 60


class TerminalManager:
    """Sessões vivas do processo. Uma por WebSocket."""

    def __init__(self) -> None:
        self._sessoes: dict[str, SessaoTerminal] = {}

    def registrar(self, chave: str, sessao: SessaoTerminal) -> None:
        self._sessoes[chave] = sessao

    def remover(self, chave: str) -> None:
        self._sessoes.pop(chave, None)

    def ativas(self) -> list[dict]:
        return [
            {
                "chave": chave,
                "host": s.host.name,
                "usuario": s.usuario,
                "ip": s.ip,
                "ocioso_s": int(s.ocioso_ha),
                "bytes_in": s.bytes_in,
                "bytes_out": s.bytes_out,
            }
            for chave, s in self._sessoes.items()
        ]

    async def encerrar_todas(self) -> None:
        for sessao in list(self._sessoes.values()):
            await sessao.fechar("painel reiniciado")
        self._sessoes.clear()

    async def varrer_ociosas(self) -> int:
        """Derruba sessões esquecidas abertas. Roda em laço de fundo."""
        encerradas = 0
        for chave, sessao in list(self._sessoes.items()):
            if sessao.expirou:
                await sessao.fechar("inatividade")
                self._sessoes.pop(chave, None)
                encerradas += 1
        return encerradas
