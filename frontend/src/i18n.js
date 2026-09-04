/**
 * Idioma da interface.
 *
 * Mesma decisão do tema: é preferência de quem olha a tela, fica no
 * navegador (localStorage) e não no banco — duas pessoas na mesma
 * instalação podem querer idiomas diferentes, e trocar o idioma não
 * deveria exigir permissão de configuração.
 *
 * **Estado honesto desta tradução:** o esqueleto do painel (menu, rodapé,
 * títulos de tela, componentes comuns, login) está nos dois idiomas. O
 * miolo das telas — tabelas, avisos e as explicações longas — segue em
 * português e vai sendo traduzido tela a tela. Preferi mecanismo pronto e
 * cobertura declarada a um `t()` em tudo devolvendo texto inexistente.
 *
 * Chave que não existe no dicionário devolve o texto em português, nunca a
 * própria chave: um rótulo faltando aparece na língua errada, e não como
 * `menu.painel` no meio da tela.
 */
import FRASES from "./i18n_frases";

const CHAVE = "faceops_idioma";
const IDIOMAS = ["pt", "en"];

export const NOMES_IDIOMA = { pt: "Português", en: "English" };

const DICIONARIO = {
  // ── Menu ────────────────────────────────────────────────────────────
  "menu.operacao": { pt: "Operação", en: "Operations" },
  "menu.monitoramento": { pt: "Monitoramento", en: "Monitoring" },
  "menu.dispositivos": { pt: "Dispositivos", en: "Devices" },
  "menu.ferramentas": { pt: "Ferramentas", en: "Tools" },
  "menu.backup": { pt: "Backup", en: "Backup" },
  "menu.administracao": { pt: "Administração", en: "Administration" },
  "menu.painel": { pt: "Painel", en: "Dashboard" },
  "menu.rastreio": { pt: "Rastreio", en: "Trace" },
  "menu.monitor": { pt: "Monitor", en: "Monitor" },
  "menu.diagnostico": { pt: "Diagnóstico", en: "Diagnostics" },
  "menu.notificacoes": { pt: "Avisos (Telegram)", en: "Alerts (Telegram)" },
  "menu.recursos": { pt: "Recursos", en: "Resources" },
  "menu.crescimento": { pt: "Crescimento", en: "Growth" },
  "menu.processos": { pt: "Processos", en: "Processes" },
  "menu.servicos": { pt: "Serviços", en: "Services" },
  "menu.cameras": { pt: "Licenciamento e dispositivos", en: "Licensing and devices" },
  "menu.descoberta": { pt: "Descoberta", en: "Discovery" },
  "menu.topologia": { pt: "Topologia", en: "Topology" },
  "menu.logs": { pt: "Logs ao vivo", en: "Live logs" },
  "menu.manutencao": { pt: "Manutenção", en: "Maintenance" },
  "menu.terminal": { pt: "InTerminal", en: "InTerminal" },
  "menu.backups": { pt: "Backups", en: "Backups" },
  "menu.agendamentos": { pt: "Agendamentos", en: "Schedules" },
  "menu.destinos": { pt: "Destinos", en: "Destinations" },
  "menu.servidores": { pt: "Servidores", en: "Servers" },
  "menu.usuarios": { pt: "Usuários", en: "Users" },
  "menu.auditoria": { pt: "Auditoria", en: "Audit" },
  "menu.config": { pt: "Configurações", en: "Settings" },

  // ── Rodapé da barra lateral ─────────────────────────────────────────
  "rodape.sair": { pt: "Sair", en: "Sign out" },
  "rodape.tema_claro": { pt: "Tema claro", en: "Light theme" },
  "rodape.tema_escuro": { pt: "Tema escuro", en: "Dark theme" },
  "rodape.trocar_claro": { pt: "Trocar para o tema claro", en: "Switch to the light theme" },
  "rodape.trocar_escuro": { pt: "Trocar para o tema escuro", en: "Switch to the dark theme" },
  "rodape.idioma": { pt: "Idioma da interface", en: "Interface language" },
  "rodape.versao": {
    pt: "Versão do painel no ar. A revisão é o commit curto do git, carimbado pelo deploy.",
    en: "Running panel version. The revision is the short git commit, stamped at deploy time.",
  },
  "rodape.bundle_defasado": {
    pt: "recarregue (Ctrl+F5)",
    en: "reload (Ctrl+F5)",
  },

  // ── Faixa da senha de fábrica ───────────────────────────────────────
  "senha.aviso_1": { pt: "Este usuário ainda está com a", en: "This account still uses the" },
  "senha.aviso_forte": { pt: "senha de fábrica", en: "factory password" },
  "senha.aviso_2": {
    pt: "Qualquer pessoa com acesso à rede consegue entrar no painel e nos servidores.",
    en: "Anyone with network access can get into the panel and into the servers.",
  },
  "senha.trocar": { pt: "Trocar senha", en: "Change password" },
  "senha.atual": { pt: "Senha atual", en: "Current password" },
  "senha.nova": { pt: "Senha nova", en: "New password" },
  "senha.confirmar": { pt: "Confirmar senha nova", en: "Confirm new password" },
  "senha.minimo": { pt: "Mínimo de 6 caracteres.", en: "At least 6 characters." },
  "senha.nao_confere": {
    pt: "A confirmação não confere com a senha nova.",
    en: "The confirmation does not match the new password.",
  },

  // ── Comuns ──────────────────────────────────────────────────────────
  "comum.cancelar": { pt: "Cancelar", en: "Cancel" },
  "comum.salvar": { pt: "Salvar", en: "Save" },
  "comum.salvando": { pt: "Salvando…", en: "Saving…" },
  "comum.carregando": { pt: "Carregando…", en: "Loading…" },
  "comum.tentar": { pt: "Tentar de novo", en: "Try again" },
  "comum.nada": { pt: "Nada por aqui", en: "Nothing here" },

  // ── Login ───────────────────────────────────────────────────────────
  "login.usuario": { pt: "Usuário", en: "Username" },
  "login.senha": { pt: "Senha", en: "Password" },
  "login.entrar": { pt: "Entrar", en: "Sign in" },
  "login.entrando": { pt: "Entrando…", en: "Signing in…" },

  // ── Títulos e subtítulos das telas ──────────────────────────────────
  "tela.painel": { pt: "Painel", en: "Dashboard" },
  "tela.painel.sub": {
    pt: "Situação dos servidores Face Detect e do último backup de cada um",
    en: "State of the Face Detect servers and of each one's latest backup",
  },
  "tela.monitor": { pt: "Monitor", en: "Monitor" },
  "tela.monitor.sub": {
    pt: "Estado dos servidores, atualizado sozinho a cada 10 segundos",
    en: "Server state, refreshing on its own every 10 seconds",
  },
  "tela.recursos": { pt: "Recursos", en: "Resources" },
  "tela.recursos.sub": {
    pt: "Leitura direta da máquina no momento do clique — sem Zabbix, sem histórico",
    en: "Read straight from the machine when you click — no Zabbix, no history",
  },
  "tela.processos": { pt: "Processos ao vivo", en: "Live processes" },
  "tela.processos.sub": {
    pt: "Quem está usando a máquina agora — como o htop, só que explicado",
    en: "Who is using the machine right now — like htop, but explained",
  },
  "tela.servicos": { pt: "Serviços", en: "Services" },
  "tela.servicos.sub": {
    pt: "Containers do Face Detect — estado, saúde e reinícios",
    en: "Face Detect containers — state, health and restarts",
  },
  "tela.cameras": { pt: "Licenciamento e dispositivos", en: "Licensing and devices" },
  "tela.cameras.sub": {
    pt: "Licença, dispositivos cadastrados, última comunicação e volume de eventos",
    en: "License, registered devices, last contact and event volume",
  },
  "tela.descoberta": { pt: "Descoberta", en: "Discovery" },
  "tela.descoberta.sub": {
    pt: "O que roda no servidor — bancos, containers, portas, GPU e disco",
    en: "What runs on the server — databases, containers, ports, GPU and disk",
  },
  "tela.topologia": { pt: "Topologia", en: "Topology" },
  "tela.topologia.sub": {
    pt: "Como o Face Detect se distribui entre os servidores — fluxo e dependências",
    en: "How Face Detect spreads across the servers — flow and dependencies",
  },
  "tela.logs": { pt: "Logs ao vivo", en: "Live logs" },
  "tela.logs.sub": {
    pt: "Acompanha qualquer container, com visões salvas e compartilhadas",
    en: "Follow any container, with saved and shared views",
  },
  "tela.manutencao": { pt: "Manutenção", en: "Maintenance" },
  "tela.manutencao.sub": {
    pt: "Disco e log dos servidores — diagnóstico e correção sem linha de comando",
    en: "Server disk and logs — diagnosis and fixes without a command line",
  },
  "tela.terminal": { pt: "InTerminal", en: "InTerminal" },
  "tela.terminal.sub": {
    pt: "Shell SSH real no servidor, pelo navegador — toda sessão é gravada",
    en: "A real SSH shell on the server, from the browser — every session is recorded",
  },
  "tela.backups": { pt: "Backups", en: "Backups" },
  "tela.backups.sub": {
    pt: "Execuções sob demanda e disparadas por agendamento",
    en: "On-demand runs and runs fired by a schedule",
  },
  "tela.agendamentos": { pt: "Agendamentos", en: "Schedules" },
  "tela.agendamentos.sub": {
    pt: "Recorrência programada de backup — o que a plataforma da NtechLab não tem",
    en: "Scheduled backup recurrence — what the NtechLab platform does not offer",
  },
  "tela.destinos": { pt: "Destinos de backup", en: "Backup destinations" },
  "tela.destinos.sub": {
    pt: "Onde os artefatos são guardados — local e externo, configurável aqui",
    en: "Where artifacts are kept — local and external, configurable here",
  },
  "tela.servidores": { pt: "Servidores", en: "Servers" },
  "tela.servidores.sub": {
    pt: "VMs do Face Detect — credenciais no cofre, identidade fixada por chave",
    en: "Face Detect VMs — credentials in the vault, identity pinned by host key",
  },
  "tela.usuarios": { pt: "Usuários", en: "Users" },
  "tela.usuarios.sub": {
    pt: "Quem entra no painel e o que cada perfil pode fazer nos servidores",
    en: "Who signs into the panel and what each role may do on the servers",
  },
  "tela.auditoria": { pt: "Auditoria", en: "Audit" },
  "tela.auditoria.sub": {
    pt: "Quem fez o quê nos servidores — e as gravações das sessões de terminal",
    en: "Who did what on the servers — plus the terminal session recordings",
  },
  "tela.config": { pt: "Configurações", en: "Settings" },
  "tela.config.sub": {
    pt: "Ajustes do painel — valem na hora, sem reiniciar nada",
    en: "Panel settings — they take effect immediately, nothing restarts",
  },
};

/** O idioma salvo; na falta dele, o do navegador; na falta dos dois, pt. */
export function idiomaAtual() {
  let salvo = null;
  try {
    salvo = localStorage.getItem(CHAVE);
  } catch {
    /* armazenamento bloqueado */
  }
  if (IDIOMAS.includes(salvo)) return salvo;
  const nav = (navigator.language || "pt").slice(0, 2).toLowerCase();
  return IDIOMAS.includes(nav) ? nav : "pt";
}

/**
 * Troca o idioma e recarrega a página.
 *
 * Recarregar é deliberado: metade dos textos ainda é literal em português
 * dentro das telas, e trocar só o que passa pelo `t()` deixaria a tela
 * misturada de um jeito pior que recarregar. É meio segundo, e a escolha
 * fica salva. (Sessão de terminal aberta cai — o botão avisa disso.)
 */
export function definirIdioma(idioma) {
  const alvo = IDIOMAS.includes(idioma) ? idioma : "pt";
  try {
    localStorage.setItem(CHAVE, alvo);
  } catch {
    /* sem armazenamento: nem adianta recarregar */
  }
  window.location.reload();
}

/**
 * Traduz.
 *
 * Duas fontes, nesta ordem: o dicionário por chave (menu, títulos de tela
 * — texto que não existe literal no JSX) e o dicionário por frase, onde a
 * chave é o próprio português da tela.
 *
 * Sem tradução, devolve o português. Nunca a chave crua: rótulo faltando
 * aparece na língua errada, e não como `menu.painel` no meio da tela.
 */
export function t(chave, padrao = "") {
  const idioma = idiomaAtual();
  const entrada = DICIONARIO[chave];
  if (entrada) return entrada[idioma] || entrada.pt || padrao || chave;
  if (idioma !== "pt" && FRASES[chave]) return FRASES[chave];
  return padrao || chave;
}

export { IDIOMAS };
