"""
Freio de força bruta no login.

Sem isto, um painel exposto na rede interna aceita tentativa ilimitada de
senha. Com a senha de fábrica documentada (`admin123`), qualquer script
acha em segundos — e quem entra no painel tem, por consequência, as
chaves SSH dos servidores do Face Detect.

Contagem em memória, sem dependência nova. Some no restart, o que é
aceitável: o objetivo é frear o ataque automatizado, não manter registro
forense — para isso existe a auditoria, que grava toda tentativa.
"""
import time
from collections import defaultdict

# Por (ip, usuário): quantas falhas antes de barrar, e por quanto tempo
FALHAS_POR_CONTA = 5
JANELA_S = 5 * 60
BLOQUEIO_S = 15 * 60

# Por IP, somando todos os usuários — pega quem varre nomes de conta
FALHAS_POR_IP = 20
BLOQUEIO_IP_S = 15 * 60


class FreioLogin:
    def __init__(self) -> None:
        self._falhas: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._falhas_ip: dict[str, list[float]] = defaultdict(list)
        self._ultima_limpeza = time.monotonic()

    def _limpar(self) -> None:
        """Descarta registro velho. Roda no máximo a cada 60s."""
        agora = time.monotonic()
        if agora - self._ultima_limpeza < 60:
            return
        self._ultima_limpeza = agora
        corte = agora - max(JANELA_S, BLOQUEIO_S, BLOQUEIO_IP_S)

        for chave in list(self._falhas):
            self._falhas[chave] = [t for t in self._falhas[chave] if t > corte]
            if not self._falhas[chave]:
                del self._falhas[chave]
        for ip in list(self._falhas_ip):
            self._falhas_ip[ip] = [t for t in self._falhas_ip[ip] if t > corte]
            if not self._falhas_ip[ip]:
                del self._falhas_ip[ip]

    def bloqueado(self, ip: str, usuario: str) -> tuple[bool, int]:
        """(está bloqueado?, segundos restantes)"""
        self._limpar()
        agora = time.monotonic()

        recentes_ip = [t for t in self._falhas_ip[ip] if agora - t < BLOQUEIO_IP_S]
        if len(recentes_ip) >= FALHAS_POR_IP:
            return True, int(BLOQUEIO_IP_S - (agora - recentes_ip[-FALHAS_POR_IP]))

        chave = (ip, usuario.lower())
        recentes = [t for t in self._falhas[chave] if agora - t < BLOQUEIO_S]
        if len(recentes) >= FALHAS_POR_CONTA:
            return True, int(BLOQUEIO_S - (agora - recentes[-FALHAS_POR_CONTA]))

        return False, 0

    def registrar_falha(self, ip: str, usuario: str) -> int:
        """Registra e devolve quantas tentativas ainda restam."""
        agora = time.monotonic()
        chave = (ip, usuario.lower())
        self._falhas[chave].append(agora)
        self._falhas_ip[ip].append(agora)

        recentes = [t for t in self._falhas[chave] if agora - t < JANELA_S]
        return max(0, FALHAS_POR_CONTA - len(recentes))

    def registrar_sucesso(self, ip: str, usuario: str) -> None:
        """Login certo zera a contagem daquela conta naquele IP."""
        self._falhas.pop((ip, usuario.lower()), None)


freio = FreioLogin()
