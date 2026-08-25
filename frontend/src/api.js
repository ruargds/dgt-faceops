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
    const detalhe =
      (corpo && corpo.detail) ||
      (typeof corpo === "string" && corpo) ||
      `Erro ${resposta.status}`;
    throw new ApiError(
      Array.isArray(detalhe) ? detalhe.map((d) => d.msg || d).join("; ") : detalhe,
      resposta.status,
      corpo
    );
  }
  return corpo;
}

const get = (c) => request(c);
const post = (c, corpo) => request(c, { method: "POST", body: JSON.stringify(corpo || {}) });
const patch = (c, corpo) => request(c, { method: "PATCH", body: JSON.stringify(corpo || {}) });
const del = (c) => request(c, { method: "DELETE" });

/** Envio de arquivo — sem Content-Type, o navegador monta o boundary. */
export async function enviarLogo(tipo, arquivo) {
  const forma = new FormData();
  forma.append("arquivo", arquivo);
  return request(`/marca/${tipo}`, { method: "POST", body: forma });
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

  // Configuração do painel
  config: () => get("/config"),
  configPublico: () => get("/config/publico"),
  marcaSituacao: () => get("/marca"),
  removerLogo: (tipo) => del(`/marca/${tipo}`),
  salvarConfig: (valores) => patch("/config", { valores }),
  restaurarConfig: (chave) => del(`/config/${chave}`),

  // Servidores
  hosts: () => get("/hosts"),
  host: (id) => get(`/hosts/${id}`),
  scanChave: (address, ssh_port) => post("/hosts/scan-chave", { address, ssh_port }),
  criarHost: (d) => post("/hosts", d),
  atualizarHost: (id, d) => patch(`/hosts/${id}`, d),
  removerHost: (id) => del(`/hosts/${id}`),
  testarHost: (id) => post(`/hosts/${id}/testar`),

  // Painel e recursos
  painel: () => get("/painel"),
  metricas: (id) => get(`/metrics/${id}`),
  metricasTodos: () => get("/metrics"),
  armazenamento: (id) => get(`/metrics/${id}/armazenamento`),

  // Serviços
  servicos: (id) => get(`/services/${id}`),
  logsContainer: (id, container, linhas = 200) =>
    get(`/services/${id}/logs/${encodeURIComponent(container)}?linhas=${linhas}`),
  reiniciarContainer: (id, container) => post(`/services/${id}/restart`, { container }),
  acaoStack: (id, acao, confirmar_host) => post(`/services/${id}/stack`, { acao, confirmar_host }),

  // Manutenção de disco e log
  diagnostico: (id) => get(`/manutencao/${id}`),
  contencaoLog: (id, d) => post(`/manutencao/${id}/contencao`, d),
  arquivarLog: (id, d) => post(`/manutencao/${id}/arquivar`, d),
  faxinaPrevia: () => get("/manutencao/faxina/previa"),
  faxinaExecutar: () => post("/manutencao/faxina/executar"),

  // Backups
  dispararBackup: (id, d) => post(`/backups/${id}`, d),
  backups: (params = "") => get(`/backups${params}`),
  backup: (runId) => get(`/backups/${runId}`),
  removerBackup: (runId) => del(`/backups/${runId}`),
  urlDownload: (runId) => `/api/backups/${runId}/download`,
  armazenamentoPainel: () => get("/backups-armazenamento"),

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

  // InTerminal
  ticketTerminal: (hostId) => post(`/terminal/ticket/${hostId}`),
  sessoesAtivas: () => get("/terminal/ativas"),
  sessoesTerminal: (params = "") => get(`/terminal/sessoes${params}`),
  urlGravacao: (id) => `/api/terminal/sessoes/${id}/gravacao`,

  // Auditoria
  auditoria: (params = "") => get(`/auditoria${params}`),
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
