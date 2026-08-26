"""
Configuração editável pela web.

Três camadas, nessa ordem de precedência:

    banco  >  variável de ambiente (.env)  >  padrão do catálogo

O `.env` continua servindo para o que precisa existir ANTES do banco
subir (senha do Postgres, SECRET_KEY, porta). Todo o resto passa para
cá, onde muda sem editar arquivo e sem reiniciar container.

O catálogo é **hardcoded**, como as permissões. Adicionar uma opção é
uma linha aqui — a tela se monta sozinha a partir dela, com rótulo, tipo,
validação e texto de ajuda. Foi feito assim de propósito: opção que só
existe no banco vira campo órfão que ninguém sabe para que serve.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.configuracao import Configuracao

log = logging.getLogger("faceops.config")


class ItemConfig:
    """Uma opção do catálogo."""

    def __init__(
        self,
        chave: str,
        categoria: str,
        rotulo: str,
        tipo: str,
        padrao,
        ajuda: str = "",
        minimo: int | None = None,
        maximo: int | None = None,
        opcoes: list[str] | None = None,
        env: str | None = None,
    ) -> None:
        self.chave = chave
        self.categoria = categoria
        self.rotulo = rotulo
        self.tipo = tipo  # texto | numero | booleano | escolha
        self.padrao = padrao
        self.ajuda = ajuda
        self.minimo = minimo
        self.maximo = maximo
        self.opcoes = opcoes or []
        # Variável do .env que serve de padrão, quando existe
        self.env = env

    def valor_padrao(self):
        if self.env and hasattr(settings, self.env):
            return getattr(settings, self.env)
        return self.padrao

    def converter(self, bruto: str):
        """Texto do banco -> valor tipado. Nunca levanta: cai no padrão."""
        try:
            if self.tipo == "numero":
                return int(bruto)
            if self.tipo == "booleano":
                return str(bruto).strip().lower() in ("1", "true", "sim", "yes")
            return str(bruto)
        except (ValueError, TypeError):
            return self.valor_padrao()

    def validar(self, valor) -> tuple[bool, str]:
        if self.tipo == "numero":
            try:
                n = int(valor)
            except (ValueError, TypeError):
                return False, f"{self.rotulo}: informe um número inteiro"
            if self.minimo is not None and n < self.minimo:
                return False, f"{self.rotulo}: mínimo {self.minimo}"
            if self.maximo is not None and n > self.maximo:
                return False, f"{self.rotulo}: máximo {self.maximo}"
        elif self.tipo == "escolha":
            if str(valor) not in self.opcoes:
                return False, f"{self.rotulo}: use um de {self.opcoes}"
        elif self.tipo == "texto":
            if len(str(valor)) > 512:
                return False, f"{self.rotulo}: no máximo 512 caracteres"
        return True, ""

    def as_dict(self, valor) -> dict:
        return {
            "chave": self.chave,
            "categoria": self.categoria,
            "rotulo": self.rotulo,
            "tipo": self.tipo,
            "ajuda": self.ajuda,
            "minimo": self.minimo,
            "maximo": self.maximo,
            "opcoes": self.opcoes,
            "valor": valor,
            "padrao": self.valor_padrao(),
        }


# ── Categorias, na ordem em que aparecem na tela ───────────────────────

CATEGORIAS = {
    "projeto": (
        "Identidade do projeto",
        "Nome e descrição que aparecem no painel. Trocar aqui adapta a "
        "instalação para outro cliente ou outro ambiente.",
    ),
    "servidores": (
        "Padrões dos servidores",
        "Valores usados quando o servidor não define o seu. O caminho de "
        "instalação é detectado sozinho no teste de conexão — isto aqui é "
        "só o palpite inicial.",
    ),
    "backup": (
        "Backup",
        "Retenção padrão por perfil e limites de execução. A retenção do "
        "agendamento e a do destino têm precedência sobre estes valores.",
    ),
    "logs": (
        "Logs ao vivo",
        "Limites do streaming. Servem para a tela não travar com container "
        "que despeja milhares de linhas por segundo.",
    ),
    "terminal": (
        "InTerminal",
        "Gravação de sessão e queda por inatividade.",
    ),
    "manutencao": (
        "Manutenção de disco e log",
        "Valores aplicados quando você usa a contenção de log. O manual da "
        "NtechLab sugere 3 GB para o journald.",
    ),
    "sessao": (
        "Sessão e acesso",
        "Duração do login e alerta de disco.",
    ),
    "monitor": (
        "Monitoramento contínuo",
        "O coletor de fundo que alimenta os gráficos. Uma execução SSH por "
        "servidor por ciclo, sequencial, e só nos servidores marcados para "
        "monitorar. Intervalo curto demais não traz informação nova — a "
        "carga de uma máquina não muda a cada 5 segundos.",
    ),
    "alerta": (
        "Limiares de alerta",
        "Acima destes valores o painel avisa na tela, e toca um som se a "
        "aba estiver aberta. Valores conservadores geram ruído; valores "
        "altos demais avisam tarde.",
    ),
    "faxina": (
        "Faxina automática",
        "Impede o painel de crescer sem fim. Roda uma vez por dia, no "
        "horário abaixo. O artefato de backup não é tocado aqui — ele tem "
        "retenção própria, por destino.",
    ),
}


# ── O catálogo ─────────────────────────────────────────────────────────
# Adicionar uma opção = adicionar uma linha aqui.

CATALOGO: list[ItemConfig] = [
    # Identidade
    ItemConfig("projeto.nome", "projeto", "Nome do painel", "texto",
               "DGT FaceOps",
               "Aparece no título da aba e no topo das telas."),
    ItemConfig("projeto.subtitulo", "projeto", "Subtítulo", "texto",
               "Operação do FindFace Multi",
               "Linha de apoio abaixo do nome, na tela de login."),
    ItemConfig("projeto.cliente", "projeto", "Cliente ou ambiente", "texto",
               "",
               "Opcional. Útil quando a mesma equipe opera mais de uma "
               "instalação — evita agir no ambiente errado."),
    ItemConfig("projeto.cor_escura", "projeto", "Cor escura (barra lateral)", "texto",
               "#0D1F35",
               "Hexadecimal, com #. É o fundo da barra lateral e da tela "
               "de login."),
    ItemConfig("projeto.cor_primaria", "projeto", "Cor primária (botões)", "texto",
               "#1A6FC4",
               "Hexadecimal, com #. Botões principais e item de menu ativo."),
    ItemConfig("projeto.cor_destaque", "projeto", "Cor de destaque", "texto",
               "#00AEEF",
               "Hexadecimal, com #. Detalhes e cursor do terminal."),

    # Padrões dos servidores
    ItemConfig("servidores.ffmulti_dir", "servidores",
               "Diretório de instalação padrão", "texto",
               "/opt/findface-multi",
               "Palpite inicial ao cadastrar servidor. O teste de conexão "
               "corrige sozinho perguntando ao Docker.",
               env="FFMULTI_DIR"),
    ItemConfig("servidores.staging_remoto", "servidores",
               "Diretório de trabalho no servidor", "texto",
               "/var/backups/faceops",
               "Onde o artefato é montado ANTES de vir para o painel. "
               "Aponte para um disco com folga: o perfil completo precisa "
               "de ~60% do tamanho de data/ livre aqui.",
               env="REMOTE_STAGING_DIR"),
    ItemConfig("servidores.timeout_comando_s", "servidores",
               "Tempo limite de comando (segundos)", "numero",
               120, "Comandos de leitura. Backup tem limite próprio.",
               minimo=15, maximo=1800),

    # Backup
    ItemConfig("backup.retencao_config", "backup",
               "Retenção do perfil Config (dias)", "numero",
               90, "0 = nunca apagar.", minimo=0, maximo=3650,
               env="RETENTION_CONFIG_DAYS"),
    ItemConfig("backup.retencao_essencial", "backup",
               "Retenção do perfil Essencial (dias)", "numero",
               30, "0 = nunca apagar.", minimo=0, maximo=3650,
               env="RETENTION_ESSENCIAL_DAYS"),
    ItemConfig("backup.retencao_completo", "backup",
               "Retenção do perfil Completo (dias)", "numero",
               180, "0 = nunca apagar.", minimo=0, maximo=3650,
               env="RETENTION_COMPLETO_DAYS"),
    ItemConfig("backup.timeout_essencial_h", "backup",
               "Tempo limite dos perfis rápidos (horas)", "numero",
               2, "Config e Essencial. Se estourar, a execução falha.",
               minimo=1, maximo=24),
    ItemConfig("backup.timeout_completo_h", "backup",
               "Tempo limite do perfil Completo (horas)", "numero",
               8, "Copiar centenas de GB demora. Seja generoso.",
               minimo=1, maximo=48),
    ItemConfig("backup.margem_disco_pct", "backup",
               "Margem de disco exigida no Completo (%)", "numero",
               60,
               "Percentual do tamanho de data/ que precisa estar livre no "
               "servidor. Foto JPEG comprime pouco; 60% é a margem que "
               "evita encher o disco de produção de madrugada.",
               minimo=10, maximo=100),

    # Logs
    ItemConfig("logs.tail_padrao", "logs",
               "Linhas iniciais ao abrir", "numero",
               200, "0 mostra só o que chegar dali em diante.",
               minimo=0, maximo=5000),
    ItemConfig("logs.max_linhas_s", "logs",
               "Limite de linhas por segundo", "numero",
               400,
               "Acima disso as linhas são descartadas e a tela avisa "
               "quantas. Sem limite, a aba trava.",
               minimo=50, maximo=5000),
    ItemConfig("logs.max_linhas_tela", "logs",
               "Linhas mantidas na tela", "numero",
               2000, "As mais antigas somem conforme chegam novas.",
               minimo=200, maximo=20000),

    # Terminal
    ItemConfig("terminal.gravar", "terminal",
               "Gravar as sessões", "booleano",
               True,
               "Grava em asciicast v2 para auditoria. Desligar não é "
               "recomendado: a gravação é o que torna o terminal web "
               "aceitável em produção.",
               env="TERMINAL_RECORD"),
    ItemConfig("terminal.timeout_ocioso_min", "terminal",
               "Queda por inatividade (minutos)", "numero",
               30, "Shell esquecido aberto em servidor de produção.",
               minimo=5, maximo=480,
               env="TERMINAL_IDLE_TIMEOUT_MIN"),

    # Manutenção
    ItemConfig("manutencao.journald_max", "manutencao",
               "Teto do journald", "escolha",
               "2G", "O manual da NtechLab sugere 3G.",
               opcoes=["1G", "2G", "3G", "5G", "10G"]),
    ItemConfig("manutencao.logrotate_maxsize", "manutencao",
               "Rotacionar o syslog ao passar de", "escolha",
               "500M", "Rotação por tamanho, além da diária.",
               opcoes=["100M", "250M", "500M", "1G", "2G"]),
    ItemConfig("manutencao.logrotate_manter", "manutencao",
               "Arquivos rotacionados a manter", "numero",
               7, "", minimo=1, maximo=60),

    # Sessão
    ItemConfig("sessao.expiracao_min", "sessao",
               "Duração do login (minutos)", "numero",
               480, "Depois disso, é preciso entrar de novo.",
               minimo=15, maximo=10080,
               env="ACCESS_TOKEN_EXPIRE_MINUTES"),
    ItemConfig("sessao.alerta_disco_pct", "sessao",
               "Alertar quando o disco passar de (%)", "numero",
               90,
               "Acima disso o cartão do servidor fica vermelho no Painel.",
               minimo=50, maximo=99),

    # Monitor
    ItemConfig("monitor.ativo", "monitor",
               "Coletor contínuo ligado", "booleano",
               True,
               "Desligar para de alimentar os gráficos. A coleta sob "
               "demanda, no botão Atualizar, continua funcionando."),
    ItemConfig("monitor.intervalo_s", "monitor",
               "Intervalo entre coletas (segundos)", "numero",
               60,
               "Com 4 servidores a 60 s, o custo é ~1,5% de um núcleo no "
               "painel e nada mensurável nos servidores.",
               minimo=15, maximo=3600),
    ItemConfig("monitor.retencao_dias", "monitor",
               "Guardar histórico por (dias)", "numero",
               30,
               "Uma amostra ocupa ~80 bytes. 4 servidores a 60 s por 30 "
               "dias dão alguns MB.",
               minimo=1, maximo=365),
    ItemConfig("monitor.som_alerta", "monitor",
               "Alerta sonoro", "booleano",
               True,
               "Toca um som quando surge alerta novo, se a aba estiver "
               "aberta. O som é gerado pelo navegador — não há arquivo."),

    # Limiares
    ItemConfig("alerta.disco_pct", "alerta",
               "Disco acima de (%)", "numero", 90,
               "Acima de 95% vira crítico automaticamente.",
               minimo=50, maximo=99),
    ItemConfig("alerta.mem_pct", "alerta",
               "Memória acima de (%)", "numero", 90,
               "Descontando cache e buffers, como deve ser.",
               minimo=50, maximo=99),
    ItemConfig("alerta.swap_pct", "alerta",
               "Swap acima de (%)", "numero", 50,
               "Swap em uso num servidor de reconhecimento facial "
               "significa latência de disco no caminho dos dados.",
               minimo=1, maximo=99),
    ItemConfig("alerta.cpu_pct", "alerta",
               "Carga por núcleo acima de (%)", "numero", 90,
               "90% equivale a 0,90 de carga por núcleo. Acima de 1,00 há "
               "processo esperando CPU.",
               minimo=50, maximo=200),
    ItemConfig("alerta.gpu_mem_pct", "alerta",
               "Memória de vídeo acima de (%)", "numero", 92,
               "Perto do limite, a próxima câmera causa falha de alocação "
               "e o worker entra em ciclo de reinício.",
               minimo=50, maximo=99),
    ItemConfig("alerta.gpu_temp", "alerta",
               "Temperatura da GPU acima de (°C)", "numero", 85,
               "Acima de 85 °C costuma haver throttling.",
               minimo=50, maximo=110),

    # Faxina
    ItemConfig("faxina.hora", "faxina",
               "Hora em que a faxina roda", "numero",
               4,
               "Uma vez por dia, nesta hora. Escolha um horário fora da "
               "janela dos backups.",
               minimo=0, maximo=23),
    ItemConfig("faxina.gravacoes_dias", "faxina",
               "Guardar gravações do terminal por (dias)", "numero",
               90,
               "Um arquivo .cast por sessão. 0 = nunca apagar — só use se "
               "houver exigência de auditoria que justifique.",
               minimo=0, maximo=3650),
    ItemConfig("faxina.auditoria_dias", "faxina",
               "Guardar auditoria por (dias)", "numero",
               365,
               "Registro de nível crítico fica o TRIPLO deste prazo: é o "
               "que interessa numa investigação, e é fração do volume.",
               minimo=0, maximo=3650),
    ItemConfig("faxina.log_execucao_dias", "faxina",
               "Guardar o log das execuções de backup por (dias)", "numero",
               60,
               "Depois disso o texto do log é esvaziado, mas a linha do "
               "histórico permanece. O log é o que pesa; a linha, não.",
               minimo=0, maximo=3650),
]

POR_CHAVE: dict[str, ItemConfig] = {i.chave: i for i in CATALOGO}


class ConfigService:
    """
    Instância única em `app.state.config`.

    Mantém um cache em memória para a leitura ser barata — a configuração
    é consultada em caminho quente (cada backup, cada stream de log) e
    não pode custar uma ida ao banco toda vez.
    """

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._carregado = False

    async def carregar(self, db: AsyncSession) -> None:
        resultado = await db.execute(select(Configuracao))
        self._cache = {c.chave: c.valor for c in resultado.scalars().all()}
        self._carregado = True
        log.info("configuracao carregada: %d valor(es) no banco", len(self._cache))

    def get(self, chave: str):
        """Valor efetivo: banco > .env > padrão do catálogo."""
        item = POR_CHAVE.get(chave)
        if item is None:
            raise KeyError(f"chave fora do catalogo: {chave}")
        if chave in self._cache:
            return item.converter(self._cache[chave])
        return item.valor_padrao()

    def tudo(self) -> list[dict]:
        """Catálogo com os valores atuais, para a tela montar sozinha."""
        return [i.as_dict(self.get(i.chave)) for i in CATALOGO]

    async def definir(
        self, db: AsyncSession, chave: str, valor, usuario: str
    ) -> tuple[bool, str]:
        item = POR_CHAVE.get(chave)
        if item is None:
            return False, f"opção desconhecida: {chave}"

        ok, erro = item.validar(valor)
        if not ok:
            return False, erro

        texto = "true" if (item.tipo == "booleano" and valor in (True, "true", 1)) \
            else ("false" if item.tipo == "booleano" else str(valor))

        existente = await db.get(Configuracao, chave)
        if existente is None:
            db.add(Configuracao(chave=chave, valor=texto, atualizado_por=usuario))
        else:
            existente.valor = texto
            existente.atualizado_por = usuario

        self._cache[chave] = texto
        return True, ""

    async def restaurar_padrao(self, db: AsyncSession, chave: str) -> bool:
        """Remove o valor do banco — volta a valer o .env ou o padrão."""
        existente = await db.get(Configuracao, chave)
        if existente is not None:
            await db.delete(existente)
        self._cache.pop(chave, None)
        return True
