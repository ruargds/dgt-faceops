"""
Cerca de destino para as saídas HTTP do painel.

O painel faz requisição para uma URL **cadastrada por gente**: a API do
Face Detect de cada servidor. Isso é, por definição, um pedido para o
servidor buscar um endereço escolhido por quem usa — o formato clássico
de SSRF.

Nesta instalação o risco tem nome e endereço: as VMs são do Azure, e todo
Azure responde em `169.254.169.254` com o **IMDS**, que entrega token de
identidade gerenciada para quem perguntar, sem autenticação nenhuma. Uma
URL apontada para lá transformaria o painel em leitor de credencial da
assinatura inteira.

Vale dizer o que esta cerca **não** é: quem cadastra servidor já tem
`hosts.manage`, e quem tem `hosts.manage` já tem SSH com sudo nos quatro
servidores. A cerca não protege contra esse administrador — protege
contra sessão sequestrada, contra erro de digitação e contra a próxima
tela que vier a aceitar URL de alguém com permissão menor.

**Limite honesto:** o cliente HTTP segue redirecionamento, e um servidor
legítimo pode responder com redirect para o IMDS. A validação aqui vale
para o endereço cadastrado; fechar o caminho do redirecionamento exigiria
trocar a política de redirect do cliente, o que quebraria instalação que
depende dela. Está registrado em `specs/pendencias.md`.
"""
import ipaddress
import socket
from urllib.parse import urlparse

# Esquemas aceitos. `file://`, `gopher://` e afins não têm o que fazer
# aqui, e são justamente os que transformam SSRF em leitura de arquivo.
ESQUEMAS = ("http", "https")

# Nomes que resolvem para o serviço de metadados nas três nuvens grandes.
# Bloqueados por nome além de por IP: DNS pode apontar qualquer nome para
# o endereço de link-local.
NOMES_PROIBIDOS = {
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "metadata",
}


class DestinoRecusado(ValueError):
    """A URL aponta para um lugar que o painel não deve alcançar."""


def _rede_proibida(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    """Devolve o motivo da recusa, ou string vazia se o IP é aceitável."""
    if ip.is_link_local:
        # 169.254.0.0/16 — onde vive o IMDS do Azure e da AWS.
        return "endereço de link-local (é onde fica o serviço de metadados da nuvem)"
    if ip.is_loopback:
        return "endereço de loopback (aponta para o próprio painel)"
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return "endereço reservado"
    return ""


def validar_url(url: str, *, permitir_privado: bool = True) -> str:
    """
    Devolve a URL normalizada, ou levanta `DestinoRecusado`.

    `permitir_privado` fica ligado de propósito: os servidores do Face Detect
    estão numa rede privada, e é exatamente para lá que o painel precisa
    falar. Recusar RFC1918 aqui quebraria o uso legítimo — o que se
    bloqueia é link-local, loopback e reservado.
    """
    texto = (url or "").strip()
    if not texto:
        raise DestinoRecusado("endereço vazio")

    partes = urlparse(texto)
    if partes.scheme.lower() not in ESQUEMAS:
        raise DestinoRecusado(
            f"use http:// ou https:// — recebi '{partes.scheme or 'sem esquema'}'"
        )
    # Usuário e senha embutidos na URL viram credencial em log de acesso e
    # em mensagem de erro. Não há caso legítimo aqui.
    if partes.username or partes.password:
        raise DestinoRecusado("não use usuário:senha dentro da URL")

    nome = (partes.hostname or "").strip().lower()
    if not nome:
        raise DestinoRecusado("endereço sem host")
    if nome in NOMES_PROIBIDOS:
        raise DestinoRecusado(f"'{nome}' é o serviço de metadados da nuvem")

    # Endereço literal: decide na hora, sem consultar DNS.
    try:
        ip = ipaddress.ip_address(nome)
    except ValueError:
        ip = None

    if ip is not None:
        motivo = _rede_proibida(ip)
        if motivo:
            raise DestinoRecusado(f"{nome} é {motivo}")
        return texto

    # Nome: resolve para conferir para onde ele aponta. Falha de DNS NÃO
    # recusa — o servidor pode estar fora no momento do cadastro, e
    # recusar por isso impediria configurar o painel antes de subir o
    # ambiente. O que se barra é o nome que resolve comprovadamente para
    # um endereço proibido.
    try:
        infos = socket.getaddrinfo(nome, None)
    except socket.gaierror:
        return texto

    for familia, *_resto, sockaddr in infos:
        try:
            resolvido = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError):
            continue
        motivo = _rede_proibida(resolvido)
        if motivo:
            raise DestinoRecusado(
                f"'{nome}' resolve para {resolvido}, que é {motivo}"
            )

    return texto
