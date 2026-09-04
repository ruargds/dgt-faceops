"""
Camada SSH agentless — nada é instalado nos servidores do Face Detect.

Decisões que valem explicação:

* **Pinagem de chave de host.** A chave pública do servidor é capturada
  numa varredura explícita (`scan_host_key`), ANTES de qualquer credencial
  trafegar. Toda conexão posterior é fixada nessa chave. Sem isso, quem
  estiver no meio da rede recebe a senha de sudo no handshake.
* **Conexão reaproveitada com TTL.** Coletar métrica de 4 hosts abrindo
  handshake por comando fica lento e enche o log de auth do servidor.
* **Segredo só em memória.** A chave PEM é decifrada no momento do uso e
  descartada. Nunca vai para log, resposta de API ou disco.
"""
import asyncio
import logging
import shlex
import time
from dataclasses import dataclass, field

import asyncssh

from app.core.vault import decrypt_secret

log = logging.getLogger("faceops.ssh")

# Referência de tamanho para a proteção contra saída gigante. O teto real
# é aplicado pelos próprios comandos (head, --tail, -d1), não por um kwarg
# do asyncssh: versões recentes removeram `max_buffer_size` do run(), e
# passá-lo quebra TODA conexão com TypeError.
MAX_OUTPUT_BYTES = 4 * 1024 * 1024

# Conexão ociosa é fechada depois disso
CONNECTION_TTL_SECONDS = 120


class SSHError(Exception):
    """Falha de conexão, autenticação ou verificação de identidade."""


class HostKeyMismatch(SSHError):
    """A chave do servidor mudou — conexão abortada, sem enviar credencial."""


@dataclass
class CommandResult:
    command: str
    exit_status: int
    stdout: str
    stderr: str
    duration_ms: int

    @property
    def ok(self) -> bool:
        return self.exit_status == 0

    def raise_for_status(self) -> "CommandResult":
        if not self.ok:
            detalhe = (self.stderr or self.stdout or "").strip()[:800]
            raise SSHError(
                f"comando falhou (exit {self.exit_status}): {self.command}\n{detalhe}"
            )
        return self


@dataclass
class _PooledConnection:
    conn: asyncssh.SSHClientConnection
    last_used: float = field(default_factory=time.monotonic)


class SSHService:
    """Instância única — mantida no estado da aplicação (app.state.ssh)."""

    def __init__(self) -> None:
        self._pool: dict[int, _PooledConnection] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        # host_id -> o docker daqui exige sudo?
        self._docker_sudo: dict[int, bool] = {}

    async def docker_needs_sudo(self, host) -> bool:
        """
        Descobre se o docker deste host exige sudo.

        Instalação padrão do Face Detect deixa o usuário de deploy FORA do
        grupo `docker` — aí `docker ps` falha com permissão negada e a
        leitura viria vazia, sem erro visível. Testar uma vez e guardar
        é mais barato que prefixar sudo em tudo (o que quebraria onde o
        usuário está no grupo e não tem sudo).
        """
        if host.id in self._docker_sudo:
            return self._docker_sudo[host.id]

        r = await self.run(host, "docker ps -q", timeout=30)
        precisa = not r.ok
        self._docker_sudo[host.id] = precisa
        return precisa

    # ── Varredura de identidade ────────────────────────────────────────

    @staticmethod
    async def scan_host_key(address: str, port: int = 22, timeout: int = 10) -> tuple[str, str]:
        """
        Busca a chave pública do servidor SEM autenticar.

        Retorna (chave_openssh, fingerprint). É o passo obrigatório antes
        de cadastrar credenciais de um host novo.
        """
        try:
            key = await asyncio.wait_for(
                asyncssh.get_server_host_key(address, port=port), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            raise SSHError(f"{address}:{port} nao respondeu em {timeout}s") from exc
        except (OSError, asyncssh.Error) as exc:
            raise SSHError(f"nao foi possivel ler a chave de {address}:{port}: {exc}") from exc

        if key is None:
            raise SSHError(f"{address}:{port} nao apresentou chave de host")

        pub = key.export_public_key("openssh").decode().strip()
        return pub, key.get_fingerprint()

    # ── Conexão ────────────────────────────────────────────────────────

    def _lock_for(self, host_id: int) -> asyncio.Lock:
        if host_id not in self._locks:
            self._locks[host_id] = asyncio.Lock()
        return self._locks[host_id]

    @staticmethod
    def _build_options(host, credencial: tuple[str, str] | None = None) -> dict:
        """
        Monta os parâmetros do asyncssh a partir do host + cofre.

        `credencial` é o par (usuário, senha) digitado na tela para uma
        sessão de terminal. Com senha preenchida, ela substitui o cofre —
        que nem é aberto — e é assim que alguém entra no servidor com a
        própria conta em vez da conta de serviço do painel. A pinagem da
        chave do host continua valendo em qualquer caso (regra 5): senha
        digitada não viaja para servidor que não é o cadastrado.
        """
        if not host.host_key_pub:
            raise SSHError(
                f"host '{host.name}' sem chave de identidade fixada. "
                "Rode a varredura de chave antes de conectar."
            )

        try:
            known = asyncssh.import_public_key(host.host_key_pub)
        except asyncssh.KeyImportError as exc:
            raise SSHError(f"chave de host de '{host.name}' ilegivel: {exc}") from exc

        opts: dict = {
            "username": host.ssh_user,
            "port": host.ssh_port,
            # (chaves_aceitas, CAs, revogadas) — pinagem estrita
            "known_hosts": ([known], [], []),
            "connect_timeout": 15,
            "keepalive_interval": 30,
        }

        # Usuário digitado vale mesmo sem senha: host com chave PEM aceita
        # a mesma chave para outra conta do servidor.
        if credencial and credencial[0]:
            opts["username"] = credencial[0]
        if credencial and credencial[1]:
            opts["password"] = credencial[1]
            # Sem isto o asyncssh tentaria a PEM do cofre primeiro, e o
            # servidor recusaria a sessão antes de ver a senha digitada.
            opts["client_keys"] = None
            return opts

        if host.auth_method == "key":
            pem = decrypt_secret(host.ssh_key_enc)
            if not pem:
                raise SSHError(f"host '{host.name}' sem chave PEM cadastrada")
            passphrase = decrypt_secret(host.ssh_key_passphrase_enc) or None
            try:
                opts["client_keys"] = [asyncssh.import_private_key(pem, passphrase)]
            except asyncssh.KeyImportError as exc:
                raise SSHError(
                    f"chave PEM de '{host.name}' invalida ou com senha errada: {exc}"
                ) from exc
        else:
            senha = decrypt_secret(host.ssh_password_enc)
            if not senha:
                raise SSHError(f"host '{host.name}' sem senha cadastrada")
            opts["password"] = senha
            # Impede o asyncssh de tentar chaves do ~/.ssh do container
            opts["client_keys"] = None

        return opts

    async def connect(self, host) -> asyncssh.SSHClientConnection:
        """Abre (ou reaproveita) uma conexão com o host."""
        async with self._lock_for(host.id):
            pooled = self._pool.get(host.id)
            if pooled is not None:
                if (
                    not pooled.conn.is_closed()
                    and time.monotonic() - pooled.last_used < CONNECTION_TTL_SECONDS
                ):
                    pooled.last_used = time.monotonic()
                    return pooled.conn
                self._discard(host.id)

            opts = self._build_options(host)
            try:
                conn = await asyncssh.connect(host.address, **opts)
            except asyncssh.HostKeyNotVerifiable as exc:
                raise HostKeyMismatch(
                    f"a chave de '{host.name}' ({host.address}) NAO confere com a "
                    "cadastrada. Conexao abortada sem enviar credenciais. "
                    "Se o servidor foi reinstalado, refaca a varredura de chave; "
                    "caso contrario, trate como incidente de seguranca."
                ) from exc
            except asyncssh.PermissionDenied as exc:
                raise SSHError(
                    f"autenticacao recusada em '{host.name}' para o usuario "
                    f"'{host.ssh_user}': {exc}"
                ) from exc
            except (OSError, asyncssh.Error) as exc:
                raise SSHError(f"falha ao conectar em '{host.name}': {exc}") from exc

            self._pool[host.id] = _PooledConnection(conn)
            return conn

    def _discard(self, host_id: int) -> None:
        pooled = self._pool.pop(host_id, None)
        if pooled is not None and not pooled.conn.is_closed():
            pooled.conn.abort()
        # Credencial ou usuário pode ter mudado — redetectar o sudo do docker
        self._docker_sudo.pop(host_id, None)

    async def disconnect(self, host_id: int) -> None:
        async with self._lock_for(host_id):
            self._discard(host_id)

    async def close_all(self) -> None:
        for host_id in list(self._pool):
            self._discard(host_id)

    # ── Execução ───────────────────────────────────────────────────────

    async def run(
        self,
        host,
        command: str,
        *,
        sudo: bool = False,
        timeout: int = 120,
        check: bool = False,
    ) -> CommandResult:
        """
        Roda um comando no host.

        `sudo=True` usa `sudo -S -p ''` com a senha pela entrada padrão —
        assim ela nunca aparece na linha de comando, e portanto nunca no
        `ps` de quem estiver logado no servidor.
        """
        # Script multilinha com sudo NAO pode passar por aqui: o alvo vira
        # `sudo -S -p '' -- <primeira linha>` e as linhas seguintes viram
        # comandos soltos, sem privilegio -- calado, sem erro visivel. Foi
        # assim que a estimativa de tamanho e a sondagem de componentes
        # internos falharam sem deixar rastro. `run_script` faz certo:
        # manda o script inteiro pela entrada padrao do `bash -s` remoto.
        if sudo and "\n" in command.strip():
            log.debug(
                "run(sudo=True) com script multilinha em '%s' -- usando run_script",
                host.name,
            )
            resultado = await self.run_script(
                host, command, sudo=sudo, timeout=timeout
            )
            return resultado.raise_for_status() if check else resultado

        conn = await self.connect(host)

        entrada: str | None = None
        alvo = command
        if sudo:
            senha = decrypt_secret(host.sudo_password_enc)
            if senha:
                alvo = f"sudo -S -p '' -- {command}"
                entrada = senha + "\n"
            else:
                # sem senha guardada, assume NOPASSWD no sudoers
                alvo = f"sudo -n -- {command}"

        inicio = time.monotonic()
        try:
            proc = await asyncio.wait_for(
                conn.run(alvo, input=entrada, check=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise SSHError(
                f"comando excedeu {timeout}s em '{host.name}': {command[:200]}"
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            # Conexão pode ter morrido — derruba do pool para reabrir depois
            await self.disconnect(host.id)
            raise SSHError(f"conexao perdida com '{host.name}': {exc}") from exc

        resultado = CommandResult(
            command=command,
            exit_status=proc.exit_status if proc.exit_status is not None else -1,
            stdout=_as_text(proc.stdout),
            stderr=_as_text(proc.stderr),
            duration_ms=int((time.monotonic() - inicio) * 1000),
        )
        return resultado.raise_for_status() if check else resultado

    async def run_script(
        self, host, script: str, *, sudo: bool = False, timeout: int = 600
    ) -> CommandResult:
        """
        Envia um script inteiro pela entrada padrão do bash remoto.

        Melhor que encadear com `&&`: preserva quebras de linha, aspas e
        heredocs, e não vaza o conteúdo no `ps` do servidor.
        """
        conn = await self.connect(host)

        alvo = "bash -s"
        entrada = script
        if sudo:
            senha = decrypt_secret(host.sudo_password_enc)
            if senha:
                # A senha vai na primeira linha, consumida pelo -S; o resto
                # do stdin fica para o bash.
                alvo = "sudo -S -p '' bash -s"
                entrada = senha + "\n" + script
            else:
                alvo = "sudo -n bash -s"

        inicio = time.monotonic()
        try:
            proc = await asyncio.wait_for(
                conn.run(alvo, input=entrada, check=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise SSHError(f"script excedeu {timeout}s em '{host.name}'") from exc
        except (OSError, asyncssh.Error) as exc:
            await self.disconnect(host.id)
            raise SSHError(f"conexao perdida com '{host.name}': {exc}") from exc

        return CommandResult(
            command="<script>",
            exit_status=proc.exit_status if proc.exit_status is not None else -1,
            stdout=_as_text(proc.stdout),
            stderr=_as_text(proc.stderr),
            duration_ms=int((time.monotonic() - inicio) * 1000),
        )

    async def run_script_stream(
        self,
        host,
        script: str,
        on_line,
        *,
        sudo: bool = False,
        timeout: int = 6 * 60 * 60,
    ) -> CommandResult:
        """
        Igual ao `run_script`, mas entrega cada linha em tempo real.

        Um backup do perfil completo leva horas. Sem streaming a tela fica
        parada e ninguém sabe se travou ou se está copiando — e o log só
        apareceria no fim, quando já não ajuda.

        `on_line` é uma corrotina chamada por linha de saída.
        """
        conn = await self.connect(host)

        alvo = "bash -s"
        entrada = script
        if sudo:
            senha = decrypt_secret(host.sudo_password_enc)
            if senha:
                alvo = "sudo -S -p '' bash -s"
                entrada = senha + "\n" + script
            else:
                alvo = "sudo -n bash -s"

        inicio = time.monotonic()
        coletadas: list[str] = []
        erros: list[str] = []

        async def _bombear() -> int:
            proc = await conn.create_process(
                alvo, stdin=asyncssh.PIPE, stderr=asyncssh.STDOUT
            )
            proc.stdin.write(entrada)
            proc.stdin.write_eof()

            async for linha in proc.stdout:
                texto = linha.rstrip("\n")
                coletadas.append(texto)
                # Teto de memória: um script barulhento não pode encher o
                # painel. Mantém o começo e vai descartando o meio.
                if len(coletadas) > 20000:
                    del coletadas[5000:10000]
                try:
                    await on_line(texto)
                except Exception:
                    # Falha ao reportar progresso nunca derruba o backup
                    pass

            await proc.wait()
            return proc.exit_status if proc.exit_status is not None else -1

        try:
            status = await asyncio.wait_for(_bombear(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            await self.disconnect(host.id)
            raise SSHError(
                f"script excedeu {timeout}s em '{host.name}' — conexao encerrada"
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            await self.disconnect(host.id)
            raise SSHError(f"conexao perdida com '{host.name}': {exc}") from exc

        return CommandResult(
            command="<script-stream>",
            exit_status=status,
            stdout="\n".join(coletadas),
            stderr="\n".join(erros),
            duration_ms=int((time.monotonic() - inicio) * 1000),
        )

    async def test(self, host) -> dict:
        """Teste de conectividade usado pelo botão 'Testar conexão'."""
        resultado = await self.run(
            host, "id -un; hostname -f 2>/dev/null || hostname; uname -r", timeout=20
        )
        linhas = resultado.stdout.strip().splitlines()
        return {
            "ok": resultado.ok,
            "usuario": linhas[0] if len(linhas) > 0 else "",
            "hostname": linhas[1] if len(linhas) > 1 else "",
            "kernel": linhas[2] if len(linhas) > 2 else "",
            "latencia_ms": resultado.duration_ms,
        }

    async def can_sudo(self, host) -> bool:
        """Confere se o usuário consegue escalar para root."""
        try:
            r = await self.run(host, "id -u", sudo=True, timeout=20)
        except SSHError:
            return False
        return r.ok and r.stdout.strip().endswith("0")

    # ── Transferência de arquivo ───────────────────────────────────────

    async def download(self, host, remote_path: str, local_path: str) -> int:
        """Traz um arquivo do host para o painel. Retorna o tamanho em bytes."""
        conn = await self.connect(host)
        try:
            async with conn.start_sftp_client() as sftp:
                await sftp.get(remote_path, local_path)
                stat = await sftp.stat(remote_path)
                return int(stat.size or 0)
        except (OSError, asyncssh.Error) as exc:
            raise SSHError(
                f"falha ao baixar {remote_path} de '{host.name}': {exc}"
            ) from exc

    async def upload(self, host, local_path: str, remote_path: str) -> None:
        """Envia um arquivo do painel para o host (usado pelos scripts)."""
        conn = await self.connect(host)
        try:
            async with conn.start_sftp_client() as sftp:
                await sftp.put(local_path, remote_path)
        except (OSError, asyncssh.Error) as exc:
            raise SSHError(
                f"falha ao enviar {local_path} para '{host.name}': {exc}"
            ) from exc

    async def remote_size(self, host, remote_path: str) -> int:
        r = await self.run(host, f"stat -c %s {shlex.quote(remote_path)}", timeout=30)
        try:
            return int(r.stdout.strip())
        except ValueError:
            return 0


def _as_text(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bytes):
        return valor.decode("utf-8", errors="replace")
    return str(valor)
