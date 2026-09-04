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
               "Operação do Face Detect",
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
    ItemConfig("monitor.intervalo_ocioso_s", "monitor",
               "Intervalo quando ninguém está usando (segundos)", "numero",
               300,
               "O painel não fica aberto o dia inteiro. Sem ninguém olhando, "
               "o coletor desacelera: vigiar não precisa de 60 s — uma queda "
               "detectada em 5 min avisa igual no Telegram. Abrir a tela "
               "acorda o coletor na hora, então a primeira leitura vem "
               "fresca.",
               minimo=30, maximo=3600),
    ItemConfig("monitor.ocioso_apos_min", "monitor",
               "Considerar ocioso depois de (minutos sem uso)", "numero",
               10,
               "Tempo sem NENHUMA requisição ao painel até ele entrar em "
               "modo econômico. Zero mantém sempre na velocidade normal.",
               minimo=0, maximo=240),
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

    # Crescimento — consumo que sobe sem parar
    ItemConfig("crescimento.ativo", "monitor",
               "Vigiar consumo que sobe sem parar", "booleano",
               True,
               "Detecta memória ou disco em subida contínua a partir das "
               "amostras que o coletor já gravou, projeta quando encosta no "
               "limite e diz o que quebra. Não custa ida nova ao servidor. "
               "Desligado, o painel só avisa quando o limiar for atingido — "
               "que costuma ser tarde demais para agir."),
    ItemConfig("crescimento.janela_h", "monitor",
               "Janela analisada (horas)", "numero",
               6,
               "Quanto tempo para trás a tendência é calculada. Janela curta "
               "reage rápido e confunde pico com tendência; janela longa "
               "demora a perceber e dilui a aceleração.",
               minimo=2, maximo=72),
    ItemConfig("crescimento.horizonte_h", "monitor",
               "Avisar quando o estouro couber em (horas)", "numero",
               72,
               "Se a projeção diz que o recurso encosta no limite dentro "
               "deste prazo, vira vigilância. Consumo que só estoura em "
               "meses é assunto de dimensionamento, não de plantão.",
               minimo=1, maximo=720),
    ItemConfig("crescimento.mem_pp_por_h", "monitor",
               "Memória: subida mínima para valer atenção (pontos %/hora)",
               "numero", 2,
               "Abaixo disso é variação normal de carga. Dois pontos por "
               "hora levam uma máquina de 60% a 95% em menos de um turno.",
               minimo=1, maximo=50),
    ItemConfig("crescimento.disco_pp_por_dia", "monitor",
               "Disco: subida mínima para valer atenção (pontos %/dia)",
               "numero", 1,
               "A escala do disco é outra: o que na memória se mede por "
               "hora, aqui se mede por dia. Um ponto percentual por dia num "
               "disco de 1 TB são cerca de 10 GB diários.",
               minimo=1, maximo=50),
    ItemConfig("crescimento.ciclos_para_abrir", "monitor",
               "Confirmar a subida em quantos ciclos antes de avisar",
               "numero", 3,
               "Uma leitura não abre vigilância: backup começando, câmera "
               "religando e cache do kernel produzem subidas curtas que "
               "passam sozinhas.",
               minimo=1, maximo=20),
    ItemConfig("crescimento.ciclos_para_fechar", "monitor",
               "Confirmar a estabilização em quantos ciclos antes do aviso de retorno",
               "numero", 3,
               "Simétrico ao de cima. Sem isto, o ruído de arredondamento da "
               "leitura (1 casa decimal) fazia uma vigilância real — disco "
               "crescendo devagar, de verdade — fechar e reabrir a cada "
               "poucos minutos, um aviso de 'voltou' atrás do outro para um "
               "consumo que nunca parou de subir.",
               minimo=1, maximo=20),
    ItemConfig("crescimento.rastrear_sozinho", "monitor",
               "Rastrear o culpado sozinho quando a vigilância abrir",
               "booleano", True,
               "Uma execução SSH, só leitura, com prioridade baixa de E/S. "
               "Desligado, a detecção continua e o rastreio passa a ser no "
               "clique — é o que fazer se o servidor não puder receber nem "
               "essa leitura."),
    ItemConfig("crescimento.rastrear_a_cada_min", "monitor",
               "Repetir o rastreio enquanto durar, a cada (minutos)",
               "numero", 30,
               "É o que transforma 'quem é grande' em 'quem está crescendo': "
               "dois rastreios com hora dão o ganho por hora de cada "
               "caminho. Intervalo curto demais vira `du` repetido em disco "
               "de produção.",
               minimo=5, maximo=1440),
    ItemConfig("containers.historico_ativo", "monitor",
               "Guardar memória por container", "booleano",
               True,
               "O ciclo já lê `docker stats` a cada passada para desenhar os "
               "cartões, e descartava o resultado. Guardando, o painel passa a "
               "responder QUEM está com a memória — sem comando novo no "
               "servidor. O custo é linha no banco, não carga no Face Detect."),
    ItemConfig("containers.intervalo_min", "monitor",
               "Gravar memória por container a cada (minutos)", "numero",
               5,
               "Memória de container não muda de forma interessante a cada "
               "minuto. Com 30 containers em 4 servidores, gravar a cada ciclo "
               "de 60 s daria 172 mil linhas por dia; a cada 5 min, 34 mil.",
               minimo=1, maximo=120),
    ItemConfig("containers.retencao_dias", "monitor",
               "Guardar memória por container por (dias)", "numero",
               7,
               "Curto de propósito: esta série responde 'quem está comendo a "
               "memória agora e desde quando'. O histórico longo de capacidade "
               "continua sendo o das amostras do host.",
               minimo=1, maximo=90),
    ItemConfig("discos.historico_ativo", "monitor",
               "Guardar E/S por dispositivo de disco", "booleano",
               True,
               "O `/proc/diskstats` já é lido duas vezes por ciclo para achar o "
               "disco mais castigado (ver 33_SATURACAO_DE_DISCO), e o resultado "
               "por dispositivo era jogado fora depois de escolher o pior. "
               "Guardando, o painel responde QUAL disco está saturado quando há "
               "mais de um — sem leitura nova no servidor."),
    ItemConfig("discos.intervalo_min", "monitor",
               "Gravar E/S por dispositivo a cada (minutos)", "numero",
               5,
               "Mesma lógica da memória por container: poucos dispositivos por "
               "servidor tornam isto barato, mas gravar a cada ciclo de 60 s "
               "ainda seria trabalho sem ganho — a saturação que interessa dura "
               "minutos, não segundos.",
               minimo=1, maximo=120),
    ItemConfig("discos.retencao_dias", "monitor",
               "Guardar E/S por dispositivo por (dias)", "numero",
               7,
               "Curto de propósito: responde 'qual disco está sofrendo agora e "
               "desde quando'. O alerta de saturação (`alerta.disco_util_pct`) "
               "não depende disto — só do pior dispositivo, guardado na amostra "
               "do host.",
               minimo=1, maximo=90),
    ItemConfig("crescimento.retencao_dias", "monitor",
               "Guardar vigilâncias encerradas por (dias)", "numero",
               90,
               "Uma linha por episódio, com a série medida e o último "
               "rastreio. Prazo maior que o dos incidentes de propósito: "
               "'este disco já encheu antes?' é pergunta de mês, não de "
               "semana.",
               minimo=1, maximo=730),
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
