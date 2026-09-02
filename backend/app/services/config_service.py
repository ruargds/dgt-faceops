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
    "seguranca": (
        "Sessão e acesso",
        "Por quanto tempo uma sessão vale. Prazo curto protege a estação "
        "esquecida aberta; prazo longo evita atrapalhar quem trabalha.",
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
               "FaceOps",
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
    ItemConfig("faxina.licenca_dias", "faxina",
               "Guardar histórico de licença por (dias)", "numero",
               365,
               "Uma linha por recurso por dia — alguns milhares de linhas por "
               "ano. É o que sustenta a projeção de 'quando acaba'; guardar "
               "para sempre não acrescenta nada.",
               minimo=30, maximo=1825),
    ItemConfig("monitor.som_alerta", "monitor",
               "Alerta sonoro", "booleano",
               True,
               "Toca um som quando surge alerta novo, se a aba estiver "
               "aberta. O som é gerado pelo navegador — não há arquivo."),
    ItemConfig("alerta.reincidencia_min", "alerta",
               "Considerar reincidente a partir de (quedas)", "numero", 3,
               "Quantas vezes o mesmo serviço precisa cair na janela para "
               "aparecer em Diagnóstico como problema que repete.",
               minimo=2, maximo=50),
    ItemConfig("analise.ativa", "monitor",
               "Analisar log dos serviços com problema", "booleano", True,
               "Lê o log SÓ de serviço que já está com incidente aberto, "
               "agrupa os erros por molde e casa com o catálogo de erros "
               "conhecidos. Desligado, o painel não lê log sozinho."),
    ItemConfig("analise.linhas", "monitor",
               "Linhas de log lidas por análise", "numero", 200,
               "Quanto maior, mais contexto e mais tráfego por leitura.",
               minimo=50, maximo=2000),
    ItemConfig("analise.intervalo_min", "monitor",
               "Reler o log do mesmo serviço a cada (minutos)", "numero", 5,
               "Evita reler o mesmo container a cada ciclo de 60 s enquanto "
               "o incidente dura.",
               minimo=1, maximo=120),
    ItemConfig("analise.servicos_por_ciclo", "monitor",
               "Máximo de serviços analisados por ciclo", "numero", 3,
               "Teto por host, por ciclo. Segura o custo quando muita coisa "
               "cai ao mesmo tempo.",
               minimo=1, maximo=20),
    ItemConfig("analise.retencao_dias", "monitor",
               "Guardar padrões de log por (dias)", "numero", 30,
               "Molde de log com contador — uma linha por tipo de erro, não "
               "por ocorrência.",
               minimo=1, maximo=365),
    ItemConfig("notificacao.retencao_dias", "monitor",
               "Guardar log de avisos enviados por (dias)", "numero", 14,
               "Registro de quem foi avisado e quando. É log operacional, "
               "não histórico: passado o prazo, sai.",
               minimo=1, maximo=180),
    ItemConfig("notificacao.repetir_apos_h", "monitor",
               "Repetir aviso de problema que persiste depois de (horas)", "numero", 0,
               "0 = nunca repete (padrão). Com 6, um problema que dura dias "
               "volta a avisar a cada 6h — é o repeat_interval do "
               "Alertmanager. Repetir sem pedido é o caminho mais curto para "
               "o aviso ser ignorado.",
               minimo=0, maximo=168),
    ItemConfig("incidentes.retencao_dias", "monitor",
               "Guardar histórico de indisponibilidade por (dias)", "numero",
               30,
               "Quando um serviço ou host caiu e voltou. Uma linha por "
               "evento — ocupa muito menos que as amostras.",
               minimo=1, maximo=365),

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
    ItemConfig("alerta.servico_reinicios", "alerta",
               "Serviço em loop a partir de (reinícios)", "numero", 5,
               "Container que reinicia sozinho tantas vezes conta como "
               "problema mesmo se estiver 'de pé' no instante da leitura. "
               "Pode ter exceção por serviço em Limiares.",
               minimo=1, maximo=100),
    ItemConfig("alerta.servico_indisponivel_min", "alerta",
               "Serviço parado vira crítico depois de (minutos)", "numero", 15,
               "Abaixo disso o alerta fica em atenção; passado esse tempo "
               "sem voltar, sobe para crítico. Pode ter exceção por "
               "serviço em Limiares.",
               minimo=1, maximo=1440),

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
    ItemConfig("alerta.disco_util_pct", "alerta",
               "Disco saturado acima de (% do tempo ocupado)", "numero",
               85,
               "Percentual do tempo em que o disco esteve com E/S em "
               "andamento. Acima de 85% ele está no limite: a fila cresce, "
               "a latência explode e tudo que toca disco trava junto — "
               "inclusive o SSH. Isto NÃO é ocupação em GB; disco vazio "
               "satura igual.",
               minimo=0, maximo=100),
    ItemConfig("alerta.disco_iops", "alerta",
               "Avisar quando o IOPS passar de", "numero",
               0,
               "Operações de disco por segundo. Zero desliga. Discos "
               "gerenciados de nuvem têm teto contratado (por exemplo, "
               "5000 num Premium SSD P30) — ponha aqui uns 80% do seu teto "
               "para o aviso chegar ANTES da saturação.",
               minimo=0, maximo=200000),
    ItemConfig("sessao.inatividade_min", "seguranca",
               "Encerrar sessão parada por (minutos)", "numero",
               20,
               "Tempo sem NENHUMA interação de teclado ou mouse até a sessão "
               "cair. O painel se atualizando sozinho não conta como "
               "interação — se contasse, uma tela esquecida aberta ficaria "
               "logada para sempre.",
               minimo=5, maximo=480),
    ItemConfig("sessao.maxima_h", "seguranca",
               "Tempo máximo de uma sessão (horas)", "numero",
               24,
               "Teto absoluto, contado do login. Passado ele é preciso "
               "entrar de novo, mesmo com uso contínuo — é o que impede uma "
               "sessão de se renovar indefinidamente.",
               minimo=1, maximo=720),
    ItemConfig("apuracao.ativa", "monitor",
               "Apurar a causa quando o incidente fecha", "booleano",
               True,
               "Uma leitura no servidor no momento em que ele volta: se a "
               "máquina reiniciou ou ficou ligada o tempo todo (a diferença "
               "entre chamado de VM e chamado de rede), mais kernel, journal "
               "e log do container. Um comando por incidente que fecha, no "
               "máximo dois por passada."),
    ItemConfig("apuracao.nivel", "monitor",
               "Profundidade da apuração", "escolha",
               "resumido",
               "Resumido responde 'o que foi' em poucas linhas — é o que "
               "cabe no aviso do celular. Completo guarda material de "
               "investigação: systemd em falha, dmesg, estado das "
               "interfaces, memória e disco, e mais linhas de journal e de "
               "log. Completo lê mais do servidor e grava mais no banco; "
               "vale quando se está investigando um caso, não em toda "
               "queda de todo dia.",
               opcoes=["resumido", "completo"]),
    ItemConfig("faxina.execucoes_dias", "faxina",
               "Guardar a linha das execuções de backup por (dias)", "numero",
               730,
               "Prazo mais longo que o do texto do log, e de propósito: a "
               "linha é o comprovante de que o backup rodou. Só sai a "
               "execução cujo artefato JÁ NÃO EXISTE mais — enquanto houver "
               "arquivo para restaurar, a linha que o descreve fica.",
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
