/**
 * Cliente da API do FaceOps.
 *
 * O token fica em localStorage. É o mesmo compromisso do InfraCore:
 * cookie httpOnly seria mais forte contra XSS, mas o painel roda em rede
 * interna e o WebSocket do InTerminal precisa do token em JavaScript para
 * pedir o ticket.
 */

const CHAVE_TOKEN = "faceops_token";

export const getToken = () => localStorage.getItem(CHAVE_TOKEN) || "";
export const setToken = (t) => localStorage.setItem(CHAVE_TOKEN, t);
export const clearToken = () => localStorage.removeItem(CHAVE_TOKEN);

/** Erro de API com status, para a UI diferenciar 403 de 502. */
export class ApiError extends Error {
  constructor(mensagem, status, corpo) {
    super(mensagem);
    this.name = "ApiError";
    this.status = status;
    this.corpo = corpo;
  }
}

async function request(caminho, opcoes = {}) {
  const cabecalhos = { ...(opcoes.headers || {}) };
  if (!(opcoes.body instanceof FormData)) {
    cabecalhos["Content-Type"] = "application/json";
  }
  const token = getToken();
  if (token) cabecalhos["Authorization"] = `Bearer ${token}`;

  let resposta;
  try {
    resposta = await fetch(`/api${caminho}`, { ...opcoes, headers: cabecalhos });
  } catch (e) {
    // Cancelamento explícito (AbortController) não é falha de rede: deixa
    // o chamador distinguir "operador clicou em Parar" de "painel caiu".
    if (e && e.name === "AbortError") throw e;
    throw new ApiError("Sem resposta do servidor. O painel está no ar?", 0, null);
  }

  if (resposta.status === 401) {
    clearToken();
    // Recarrega para cair na tela de login em vez de deixar a tela travada
    if (!caminho.startsWith("/auth/login")) window.location.reload();
    throw new ApiError("Sessão expirada.", 401, null);
  }

  if (resposta.status === 204) return null;

  const tipo = resposta.headers.get("content-type") || "";
  const corpo = tipo.includes("application/json")
    ? await resposta.json().catch(() => null)
    : await resposta.text();

  if (!resposta.ok) {
    throw new ApiError(mensagemDeErro(resposta.status, corpo), resposta.status, corpo);
  }
  return corpo;
}

// O que dizer quando o corpo do erro não é nosso.
//
// 502/503/504 vêm do proxy, não do painel — e durante `atualizar.sh` o
// backend REINICIA, então esse erro é esperado por alguns segundos.
const MENSAGEM_POR_STATUS = {
  400: "Requisição inválida.",
  403: "Você não tem permissão para isso.",
  404: "Não encontrado.",
  409: "Conflito: alguém alterou isso antes.",
  413: "Arquivo grande demais.",
  429: "Muitas tentativas seguidas. Espere um pouco.",
  500: "Erro interno do painel. O detalhe está no log do backend.",
  502: "O painel está reiniciando ou fora do ar. Durante uma atualização isso é esperado — tente de novo em alguns segundos.",
  503: "O painel está indisponível no momento. Tente de novo em alguns segundos.",
  504: "O painel demorou demais para responder.",
};

/**
 * Mensagem curta para um erro de API.
 *
 * Nasceu de um defeito real: o corpo do erro era usado como mensagem sem
 * conferir NADA. Quando a Cloudflare devolveu um 502, os 4 KB de HTML da
 * página de erro dela viraram a "mensagem" — e as telas, que exibem a
 * mensagem onde vai o conteúdo, mostraram uma página de erro inteira
 * dentro do cartão "Rotatividade do FindFace", como se fosse a leitura
 * do servidor.
 *
 * É a mesma família de "serviço travado" e "câmera sem evento": o painel
 * apresentando uma falha de leitura como se fosse o dado lido. Aqui a
 * regra é explícita — só vira mensagem o que for JSON nosso, ou texto
 * curto que não pareça documento.
 */
export function mensagemDeErro(status, corpo) {
  const detalhe = corpo && typeof corpo === "object" ? corpo.detail : null;
  if (detalhe) {
    return Array.isArray(detalhe)
      ? detalhe.map((d) => d.msg || d).join("; ")
      : String(detalhe);
  }

  if (typeof corpo === "string") {
    const limpo = corpo.trim();
    const pareceDocumento =
      limpo.startsWith("<") || /<\/?(html|body|head|!doctype)/i.test(limpo.slice(0, 500));
    // Teto de tamanho junto com a checagem de HTML: mensagem de erro que
    // não cabe numa linha não é mensagem, é despejo.
    if (limpo && !pareceDocumento && limpo.length <= 300) return limpo;
  }

  return MENSAGEM_POR_STATUS[status] || `Erro ${status} ao falar com o painel.`;
}

const get = (c, opts) => request(c, opts);
const post = (c, corpo) => request(c, { method: "POST", body: JSON.stringify(corpo || {}) });

/**
 * Baixa um arquivo de endpoint autenticado.
 *
 * Um <a href> comum NÃO manda o header Authorization (o token está no
 * localStorage, não em cookie), então a navegação cairia em 401. Aqui o
 * fetch anexa o Bearer, e o blob vira download via <a download> temporário.
 * Serve para arquivos pequenos (CSV, gravação .cast). Para artefatos
 * grandes de backup use o ticket de streaming, que não bufferiza memória.
 */
async function baixar(url, nomeSugerido) {
  const token = getToken();
  const resp = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (resp.status === 401) {
    clearToken();
    window.location.reload();
    throw new ApiError("Sessão expirada.", 401, null);
  }
  if (!resp.ok) {
    let detalhe = `Erro ${resp.status}`;
    try {
      const j = await resp.json();
      detalhe = j.detail || detalhe;
    } catch {
      /* corpo não-JSON */
    }
    throw new ApiError(detalhe, resp.status, null);
  }
  const blob = await resp.blob();
  let nome = nomeSugerido || "download";
  const cd = resp.headers.get("content-disposition") || "";
  const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
  if (m) nome = decodeURIComponent(m[1]);
  const objurl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objurl;
  a.download = nome;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(objurl), 10000);
}

/**
 * Baixa o artefato de backup em streaming, via ticket de uso único.
 * O navegador NAVEGA para a URL (o nginx faz streaming), sem header e sem
 * bufferizar dezenas de GB na memória.
 */
async function baixarBackup(runId) {
  const r = await post(`/backups/${runId}/download-ticket`);
  window.location.href = `/api/backups/download?ticket=${encodeURIComponent(r.ticket)}`;
}
const patch = (c, corpo) => request(c, { method: "PATCH", body: JSON.stringify(corpo || {}) });
const del = (c) => request(c, { method: "DELETE" });

/** Envio de arquivo — sem Content-Type, o navegador monta o boundary. */
export async function enviarLogo(tipo, arquivo) {
  const forma = new FormData();
  forma.append("arquivo", arquivo);
  return request(`/marca/${tipo}`, { method: "POST", body: forma });
}

/**
 * Período para a query: janela relativa ou intervalo absoluto.
 *
 * Os dois formatos existem porque as perguntas são diferentes — "as
 * últimas 6 horas" acompanha o tempo passando, "a madrugada de terça"
 * fica parada onde está. Mandar os dois seria ambíguo, então o absoluto
 * ganha quando existe, igual ao que o servidor faz.
 */
function consultaPeriodo(periodo) {
  if (periodo && periodo.de && periodo.ate) {
    return `de=${encodeURIComponent(periodo.de)}&ate=${encodeURIComponent(periodo.ate)}`;
  }
  return `horas=${(periodo && periodo.horas) || 6}`;
}

export const api = {
  // Autenticação
  login: (username, password) => post("/auth/login", { username, password }),
  me: () => get("/auth/me"),
  sair: () => post("/auth/sair"),
  catalogo: () => get("/auth/catalogo"),
  trocarSenha: (senha_atual, senha_nova) => post("/auth/trocar-senha", { senha_atual, senha_nova }),
  usuarios: () => get("/auth/usuarios"),
  criarUsuario: (d) => post("/auth/usuarios", d),
  atualizarUsuario: (id, d) => patch(`/auth/usuarios/${id}`, d),
  removerUsuario: (id) => del(`/auth/usuarios/${id}`),

  // Estado do painel — versão e revisão do que está no ar
  saude: () => get("/saude"),

  // Configuração do painel
  config: () => get("/config"),
  configPublico: () => get("/config/publico"),
  marcaSituacao: () => get("/marca"),
  removerLogo: (tipo) => del(`/marca/${tipo}`),
  salvarConfig: (valores) => patch("/config", { valores }),
  restaurarConfig: (chave) => del(`/config/${chave}`),

  // Licenciamento do FindFace — liberado, em uso, restante. Vai pela API
  // HTTP da NtechLab; o limite de licença não existe no banco lido por SSH.
  licencaFindFace: (id) => get(`/dispositivos/${id}/licenca`),

  // Rotatividade do proprio FindFace: por quanto tempo cada coisa fica.
  // E a resposta de causa para o disco que enche toda semana -- a limpeza
  // de eventos ataca o sintoma.
  // Ritmo de consumo da licenca: e a resposta de "quando acaba", que a
  // leitura do instante nao da. Amostra por dia, gravada a cada leitura.
  licencaHistorico: (id, dias = 90) =>
    get(`/dispositivos/${id}/licenca/historico?dias=${dias}`),
  // Componentes internos do FindFace, pelas portas do manual do fabricante.
  internos: (id) => get(`/descoberta/internos/${id}`),
  // Rastreio: achados com evidencia, impacto e acao. Sob demanda.
  rastreio: (hostId) =>
    get(`/descoberta/rastreio${hostId ? `?host_id=${hostId}` : ""}`),

  retencao: (id) => get(`/dispositivos/${id}/retencao`),
  // Chaves que so existem no arquivo de configuracao do FindFace.
  configFF: (id) => get(`/dispositivos/${id}/configff`),
  salvarConfigFF: (id, d) => post(`/dispositivos/${id}/configff`, d),
  salvarRetencao: (id, d) => patch(`/dispositivos/${id}/retencao`, d),

  // Servidores
  hosts: () => get("/hosts"),
  host: (id) => get(`/hosts/${id}`),
  scanChave: (address, ssh_port) => post("/hosts/scan-chave", { address, ssh_port }),
  criarHost: (d) => post("/hosts", d),
  atualizarHost: (id, d) => patch(`/hosts/${id}`, d),
  removerHost: (id) => del(`/hosts/${id}`),
  testarHost: (id) => post(`/hosts/${id}/testar`),
  testarApiHost: (id, corpo) => post(`/hosts/${id}/testar-api`, corpo),

  // Painel e recursos
  painel: () => get("/painel"),
  metricas: (id) => get(`/metrics/${id}`),
  metricasTodos: () => get("/metrics"),
  armazenamento: (id) => get(`/metrics/${id}/armazenamento`),

  // Serviços
  servicos: (id) => get(`/services/${id}`),
  logsContainer: (id, container, linhas = 200) =>
    get(`/services/${id}/logs/${encodeURIComponent(container)}?linhas=${linhas}`),
  // Parar/subir um container. `confirmar` é o nome do container, exigido
  // só em "stop": o risco não é errar a ação, é errar qual serviço.
  powerContainer: (hostId, container, acao, confirmar = "") =>
    post(`/services/${hostId}/power`, { container, acao, confirmar }),
  reiniciarContainer: (id, container) => post(`/services/${id}/restart`, { container }),
  acaoStack: (id, acao, confirmar_host) => post(`/services/${id}/stack`, { acao, confirmar_host }),

  // Manutenção de disco e log
  diagnostico: (id, opts) => get(`/manutencao/${id}`, opts),
  contencaoLog: (id, d) => post(`/manutencao/${id}/contencao`, d),
  arquivarLog: (id, d) => post(`/manutencao/${id}/arquivar`, d),
  faxinaPrevia: () => get("/manutencao/faxina/previa"),
  faxinaExecutar: () => post("/manutencao/faxina/executar"),
  // Limpeza pontual do painel: categorias marcadas, acima de N dias.
  // simular:true so conta; aplicar exige a palavra de confirmacao.
  faxinaPontual: (d) => post("/manutencao/faxina/pontual", d),
  limpezaOpcoes: (id) => get(`/manutencao/${id}/limpeza/opcoes`),
  limpezaExecutar: (id, d) => post(`/manutencao/${id}/limpeza`, d),

  // Backups
  dispararBackup: (id, d) => post(`/backups/${id}`, d),
  backupDoPainel: (destinos) => post("/backups-painel", { destinos }),
  backups: (params = "") => get(`/backups${params}`),
  backup: (runId) => get(`/backups/${runId}`),
  removerBackup: (runId) => del(`/backups/${runId}`),
  urlDownload: (runId) => `/api/backups/${runId}/download`,
  // O manifesto vive DENTRO do .tar.gz: traz o conteudo, a versao das
  // imagens e o roteiro de restauracao do fabricante. Ler sem baixar e
  // extrair e o que permite decidir antes de restaurar.
  manifesto: (runId) => get(`/backups/${runId}/manifesto`),
  // Os comandos daquele artefato naquele servidor, montados a partir do
  // manifesto: caminho real da instalacao, projeto compose e so os passos
  // que o artefato exige.
  roteiro: (runId) => get(`/backups/${runId}/roteiro`),
  importarBackup: async (arquivo, hostId) => {
    const forma = new FormData();
    forma.append("arquivo", arquivo);
    if (hostId) forma.append("host_id", String(hostId));
    return request("/backups-importar", { method: "POST", body: forma });
  },
  armazenamentoPainel: () => get("/backups-armazenamento"),
  // Que perfis fazem sentido naquele servidor. Oferecer os tres em
  // qualquer maquina produz falha garantida onde nao ha FindFace.
  perfisDoHost: (id) => get(`/backups/perfis/${id}`),
  // Quanto vai ocupar e se cabe -- medido no servidor e cruzado com o
  // tamanho real das execucoes anteriores.
  estimativa: (id) => get(`/backups/estimativa/${id}`),
  // Todos os servidores de uma vez. Cada um com o perfil que aguenta, e
  // cada artefato na pasta do seu servidor no destino.
  backupTodos: (d) => post("/backups-todos", d),
  // O que da para recuperar, por servidor: o mais recente de cada perfil,
  // a idade e o que aquele perfil recupera.
  recuperacao: () => get("/backups/recuperacao"),
  // Parar execucao em andamento e limpar o historico de falhas.
  cancelarBackup: (runId) => post(`/backups/${runId}/cancelar`),
  limparFalhas: () => del("/backups-falhas"),

  // Destinos de backup
  destinos: () => get("/destinos"),
  criarDestino: (d) => post("/destinos", d),
  atualizarDestino: (id, d) => patch(`/destinos/${id}`, d),
  removerDestino: (id) => del(`/destinos/${id}`),
  testarDestino: (id) => post(`/destinos/${id}/testar`),

  // Agendamentos
  agendamentos: () => get("/schedules"),
  criarAgendamento: (d) => post("/schedules", d),
  atualizarAgendamento: (id, d) => patch(`/schedules/${id}`, d),
  removerAgendamento: (id) => del(`/schedules/${id}`),
  executarAgendamento: (id) => post(`/schedules/${id}/executar`),

  // Logs ao vivo
  containersLog: (id) => get(`/logs/containers/${id}`),
  visoesLog: () => get("/logs/visoes"),
  criarVisaoLog: (d) => post("/logs/visoes", d),
  removerVisaoLog: (id) => del(`/logs/visoes/${id}`),
  ticketLog: (id, container, tail) =>
    post(`/logs/ticket/${id}?container=${encodeURIComponent(container)}&tail=${tail}`),

  // Monitor contínuo
  monitorResumo: () => get("/monitor/resumo"),
  // "Atualizar" vai aos servidores agora. Reler o resumo não adianta: ele
  // é cacheado por ciclo, e sem coleta nova o payload é idêntico.
  monitorColetar: () => post("/monitor/coletar", {}),
  monitorSerie: (id, horas) => get(`/monitor/serie/${id}?horas=${horas}`),
  monitorAlertas: () => get("/monitor/alertas"),
  // Horário de pico: agregação sobre o histórico já gravado, sem modelo.
  monitorPico: (hostId, dias = 14) => get(`/monitor/pico?host_id=${hostId}&dias=${dias}`),

  // Incidentes — histórico de indisponibilidade, aberto/fechado sozinho
  // pelo ciclo do monitor.
  incidentesAbertos: (hostId) =>
    get(`/incidentes/abertos${hostId ? `?host_id=${hostId}` : ""}`),
  incidentesRecentes: (dias = 3, hostId, servico) =>
    get(
      `/incidentes/recentes?dias=${dias}` +
      (hostId ? `&host_id=${hostId}` : "") +
      (servico ? `&servico=${encodeURIComponent(servico)}` : ""),
    ),
  // A apuração completa sai só quando alguém abre: a lista de 7 dias não
  // carrega o log de cada queda junto.
  apuracoesRecentes: (dias = 14) => get(`/diagnostico/apuracoes?dias=${dias}`),
  apuracaoIncidente: (id) => get(`/incidentes/${id}/apuracao`),
  apurarIncidente: (id) => post(`/incidentes/${id}/apurar`, {}),

  // Crescimento — consumo que sobe sem parar. As três primeiras leem só
  // o banco (a série que o coletor já gravou); `crescimentoRastrear` é a
  // única que abre SSH, e por isso é POST e só roda no clique.
  crescimentoAnalise: (hostId, periodo) =>
    get(`/crescimento/analise/${hostId}?${consultaPeriodo(periodo)}`),
  crescimentoContainers: (hostId, periodo, limite = 24) =>
    get(`/crescimento/containers/${hostId}?${consultaPeriodo(periodo)}&limite=${limite}`),
  crescimentoDiscos: (hostId, horas) =>
    get(`/crescimento/discos/${hostId}?horas=${horas}`),
  crescimentoVigilancias: (hostId, dias = 7) =>
    get(`/crescimento?dias=${dias}${hostId ? `&host_id=${hostId}` : ""}`),
  crescimentoRastrear: (id) => post(`/crescimento/${id}/rastrear`, {}),
  crescimentoRastrearHost: (hostId, recurso = "disco") =>
    post(`/crescimento/rastrear/${hostId}?recurso=${recurso}`, {}),

  // Diagnóstico — reincidência, padrões de log e base de erros conhecidos.
  // Tudo leitura barata, menos `analisarLog`, que lê no servidor (por isso
  // é POST e só roda no clique).
  reincidencia: (dias = 14) => get(`/diagnostico/reincidencia?dias=${dias}`),
  padroesLog: (dias = 7, hostId) =>
    get(`/diagnostico/padroes?dias=${dias}${hostId ? `&host_id=${hostId}` : ""}`),
  analisarLog: (hostId, servico) =>
    post(`/diagnostico/analisar/${hostId}?servico=${encodeURIComponent(servico)}`),
  catalogoErros: () => get("/diagnostico/catalogo"),
  limparPadroesLog: (hostId) =>
    del(`/diagnostico/padroes${hostId ? `?host_id=${hostId}` : ""}`),

  // Aviso por Telegram. O token vai no corpo e NUNCA volta — as leituras
  // trazem só o nome do bot e a impressão digital.
  notifConta: () => get("/notificacoes/conta"),
  salvarNotifConta: (d) => request("/notificacoes/conta", { method: "PUT", body: JSON.stringify(d) }),
  // Descobre os chats que já falaram com o bot — evita digitar chat_id.
  notifChats: () => get("/notificacoes/chats"),
  notifDestinos: () => get("/notificacoes/destinos"),
  salvarNotifDestino: (d) => request("/notificacoes/destinos", { method: "PUT", body: JSON.stringify(d) }),
  removerNotifDestino: (id) => del(`/notificacoes/destinos/${id}`),
  // Sem destino = testa todos. É o botão que prova o caminho inteiro.
  testarNotif: (destinoId) =>
    post(`/notificacoes/testar${destinoId ? `?destino_id=${destinoId}` : ""}`),
  notifRegras: () => get("/notificacoes/regras"),
  salvarNotifRegra: (d) => request("/notificacoes/regras", { method: "PUT", body: JSON.stringify(d) }),
  removerNotifRegra: (id) => del(`/notificacoes/regras/${id}`),
  notifEnvios: (limite = 30) => get(`/notificacoes/envios?limite=${limite}`),

  // Limiares — exceção de limite por host/serviço, por cima do padrão
  // global em Configurações.
  limiares: () => get("/limiares"),
  salvarLimiar: (d) => request("/limiares", { method: "PUT", body: JSON.stringify(d) }),
  restaurarLimiar: (id) => del(`/limiares/${id}`),

  // Dispositivos (câmeras)
  // Última interação por câmera. Chamada SÓ por clique: a varredura lê o
  // fluxo de eventos do FindFace e não tem por que rodar sozinha.
  ultimaInteracao: (id, maxEventos) =>
    get(
      `/dispositivos/${id}/ultima-interacao` +
        (maxEventos ? `?max_eventos=${encodeURIComponent(maxEventos)}` : "")
    ),
  dispositivos: (id, periodo) => get(`/dispositivos/${id}?periodo=${periodo}`),
  redescobrirDispositivos: (id) => post(`/dispositivos/${id}/redescobrir`),

  // Descoberta (inventário do servidor)
  descoberta: (id) => get(`/descoberta/${id}`),
  reiniciarCloudflared: (id) => post(`/descoberta/${id}/cloudflared/reiniciar`),
  topologia: () => get(`/descoberta/topologia/mapa`),

  // Processos ao vivo (htop didático)
  processos: (id, limite = 25) => get(`/processos/${id}?limite=${limite}`),

  // Exportações (CSV) — URLs para <a href>
  // Exportação com os MESMOS filtros da tela: um CSV que discorda do que
  // está à vista é pior que nenhum, porque ninguém desconfia dele.
  urlExportarAuditoria: (dias = 30, filtros = "") =>
    `/api/exportar/auditoria?dias=${dias}${filtros}`,
  urlExportarBackups: (dias = 90) => `/api/exportar/backups?dias=${dias}`,
  urlExportarAgendamentos: () => "/api/exportar/agendamentos",
  urlExportarSessoes: (dias = 90) => `/api/exportar/sessoes?dias=${dias}`,
  urlExportarMonitor: (id, horas = 168) => `/api/exportar/monitor/${id}?horas=${horas}`,
  urlExportarDispositivos: (id, periodo = "mes") => `/api/exportar/dispositivos/${id}?periodo=${periodo}`,

  // InTerminal
  // Credencial da sessão vai no CORPO, nunca na query string (regra 2).
  ticketTerminal: (hostId, credencial) =>
    post(`/terminal/ticket/${hostId}`, credencial || {}),
  sessoesAtivas: () => get("/terminal/ativas"),
  sessoesTerminal: (params = "") => get(`/terminal/sessoes${params}`),
  urlGravacao: (id) => `/api/terminal/sessoes/${id}/gravacao`,

  // Downloads autenticados
  baixar,
  baixarBackup,

  // Auditoria
  // Sessão: prazos em vigor e renovação. A renovação é pedida pela tela
  // SÓ quando houve interação de gente — ver `useSessaoViva`.
  politicaSessao: () => get("/auth/sessao"),
  renovarSessao: () => post("/auth/renovar", {}),
  perfis: () => get("/auth/perfis"),
  // Ações rápidas: o catálogo vem sem os comandos, e a execução manda só
  // a CHAVE. Comando arbitrário tem lugar próprio — o InTerminal, que
  // grava a sessão inteira.
  comandosDoHost: (hostId) => get(`/hosts/${hostId}/comandos`),
  executarComando: (hostId, chave, confirmar = "") =>
    post(`/hosts/${hostId}/comandos/${chave}`, { confirmar }),
  auditoria: (params = "") => get(`/auditoria${params}`),
  filtrosAuditoria: (dias = 90) => get(`/auditoria/filtros?dias=${dias}`),
  resumoAuditoria: () => get("/auditoria/resumo"),
};

/* ── Formatadores ──────────────────────────────────────────────────── */

export function formatBytes(bytes) {
  if (!bytes || bytes < 0) return "0 B";
  const unidades = ["B", "KB", "MB", "GB", "TB", "PB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), unidades.length - 1);
  const valor = bytes / Math.pow(1024, i);
  return `${valor.toFixed(valor >= 100 || i === 0 ? 0 : 1)} ${unidades[i]}`;
}

export function formatData(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return "—";
  return d.toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

export function formatDuracao(segundos) {
  if (!segundos || segundos < 0) return "—";
  const s = Math.floor(segundos);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}min ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}min`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** Verde até 70%, âmbar até 88%, vermelho acima — usado nas barras. */
export function nivel(pct) {
  if (pct >= 88) return "err";
  if (pct >= 70) return "warn";
  return "ok";
}
