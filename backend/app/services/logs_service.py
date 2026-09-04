"""
Log ao vivo pelo navegador.

Acompanhar integração em produção normalmente termina em algo como:

    docker logs -f --tail 0 macthes-faces-consumer-1 2>&1 \\
      | jq -r 'select(.trace_id) | "\\(.timestamp[11:19]) | \\(.trace_id) | …"'

Funciona, mas exige SSH, exige `jq` instalado no servidor, e o filtro vive
na memória de quem digitou. Aqui isso vira visão salva, compartilhada e
auditada.

**O `jq` não roda no servidor.** O painel transmite a linha crua e a
formatação acontece no navegador. Três razões: não depende de `jq` estar
instalado no host, não deixa a expressão do usuário chegar perto de um
shell, e permite trocar o formato sem reabrir a conexão.
"""
import re
import time
from dataclasses import dataclass, field

import asyncssh

from app.services import erros_conexao
from app.services.ssh_service import SSHError

# Nome de container: o que o Docker aceita, e nada além. O valor vai para
# a linha de comando remota, então a allowlist vem antes do shlex.
NOME_VALIDO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

# Teto de linhas por segundo entregues ao navegador. Um container em loop
# de erro despeja milhares por segundo; sem limite, a aba trava.
MAX_LINHAS_S = 400

# Sessão de log sem ninguém olhando é desperdício de conexão SSH
TIMEOUT_OCIOSO_S = 30 * 60


class LogError(Exception):
    pass


@dataclass
class SessaoLog:
    """Um `docker logs -f` vivo, ligado a um WebSocket."""

    host: object
    container: str
    usuario: str
    ip: str
    tail: int = 200

    conn: asyncssh.SSHClientConnection | None = None
    processo: asyncssh.SSHClientProcess | None = None
    linhas_enviadas: int = 0
    linhas_descartadas: int = 0
    inicio: float = field(default_factory=time.monotonic)
    _janela_inicio: float = field(default_factory=time.monotonic)
    _janela_contagem: int = 0

    async def abrir(self, precisa_sudo: bool) -> None:
        from app.services.ssh_service import SSHService

        if not NOME_VALIDO.match(self.container or ""):
            raise LogError(f"nome de container invalido: {self.container!r}")

        # Conexão dedicada, fora do pool: o `docker logs -f` fica aberto a
        # sessão inteira e não pode disputar canal com a coleta de métrica.
        opcoes = SSHService._build_options(self.host)
        try:
            self.conn = await asyncssh.connect(self.host.address, **opcoes)
        except asyncssh.HostKeyNotVerifiable as exc:
            raise SSHError(
                f"a chave de '{self.host.name}' nao confere com a cadastrada — "
                "sessao de log recusada."
            ) from exc
        except (OSError, asyncssh.Error) as exc:
            raise SSHError(
                erros_conexao.mensagem(self.host.rotulo, exc)
            ) from exc

        tail = max(0, min(int(self.tail), 5000))
        # 2>&1 porque muita aplicação escreve o log estruturado em stderr
        base = f"docker logs -f --tail {tail} {self.container} 2>&1"
        comando = f"sudo -n {base}" if precisa_sudo else base

        try:
            self.processo = await self.conn.create_process(
                comando, encoding="utf-8", errors="replace"
            )
        except (OSError, asyncssh.Error) as exc:
            await self.fechar()
            raise SSHError(f"falha ao abrir o log de '{self.container}': {exc}") from exc

    async def ler(self) -> str | None:
        """Próxima linha, ou None quando o stream termina."""
        if self.processo is None:
            return None
        try:
            linha = await self.processo.stdout.readline()
        except (asyncssh.Error, OSError):
            return None
        if not linha:
            return None

        # Limite de taxa por janela de 1s. Descartar é melhor que travar a
        # aba: a contagem do que foi descartado vai para a tela, então
        # ninguém é enganado sobre estar vendo tudo.
        agora = time.monotonic()
        if agora - self._janela_inicio >= 1.0:
            self._janela_inicio = agora
            self._janela_contagem = 0
        self._janela_contagem += 1
        if self._janela_contagem > MAX_LINHAS_S:
            self.linhas_descartadas += 1
            return ""

        self.linhas_enviadas += 1
        return linha.rstrip("\n")

    async def fechar(self) -> None:
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

    @property
    def ocioso_ha(self) -> float:
        return time.monotonic() - self.inicio


class LogManager:
    """Sessões de log vivas neste processo."""

    def __init__(self) -> None:
        self._sessoes: dict[str, SessaoLog] = {}

    def registrar(self, chave: str, sessao: SessaoLog) -> None:
        self._sessoes[chave] = sessao

    def remover(self, chave: str) -> None:
        self._sessoes.pop(chave, None)

    def ativas(self) -> list[dict]:
        return [
            {
                "chave": c,
                "host": s.host.name,
                "container": s.container,
                "usuario": s.usuario,
                "linhas": s.linhas_enviadas,
                "descartadas": s.linhas_descartadas,
                "aberta_ha_s": int(s.ocioso_ha),
            }
            for c, s in self._sessoes.items()
        ]

    async def encerrar_todas(self) -> None:
        for s in list(self._sessoes.values()):
            await s.fechar()
        self._sessoes.clear()

    async def varrer_ociosas(self) -> int:
        n = 0
        for chave, sessao in list(self._sessoes.items()):
            if sessao.ocioso_ha > TIMEOUT_OCIOSO_S:
                await sessao.fechar()
                self._sessoes.pop(chave, None)
                n += 1
        return n


async def listar_containers(ssh, host) -> list[dict]:
    """
    Containers do host, para o seletor da tela.

    Sem cerca de projeto compose aqui, de propósito: ler log é leitura, e
    o valor da tela está justamente em acompanhar a aplicação de
    integração, que não pertence ao projeto do Face Detect.
    """
    precisa_sudo = await ssh.docker_needs_sudo(host)
    r = await ssh.run(
        host,
        "docker ps -a --format "
        "'{{.Names}}|{{.Label \"com.docker.compose.project\"}}|"
        "{{.Label \"com.docker.compose.service\"}}|{{.State}}'",
        sudo=precisa_sudo,
        timeout=40,
    )
    saida: list[dict] = []
    for linha in (r.stdout or "").strip().splitlines():
        partes = linha.split("|")
        if len(partes) < 4:
            continue
        saida.append({
            "nome": partes[0],
            "projeto": partes[1] if partes[1] != "<no value>" else "",
            "servico": partes[2] if partes[2] != "<no value>" else "",
            "estado": partes[3],
        })
    saida.sort(key=lambda c: (c["projeto"], c["nome"]))
    return saida
