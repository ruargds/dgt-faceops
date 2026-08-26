/**
 * Tema claro/escuro.
 *
 * Preferência de quem olha a tela, não do cliente: fica no navegador
 * (localStorage), não no banco. Duas pessoas com perfis diferentes na
 * mesma instalação podem querer temas diferentes, e um plantão noturno
 * não deveria depender de alguém com permissão de configuração.
 *
 * Aplicado como atributo no <html> ANTES do primeiro render — decidir isso
 * dentro de um componente faria a tela nascer clara e escurecer depois, o
 * que é pior que não ter tema escuro. Por isso este módulo é importado no
 * index.js e roda no carregamento.
 *
 * Só os tokens de cor mudam (ver styles.css). Nenhum componente pergunta
 * em que tema está.
 */
const CHAVE = "faceops_tema";
const TEMAS = ["claro", "escuro"];

/** O tema salvo; na falta dele, o do sistema operacional. */
export function temaAtual() {
  let salvo = null;
  try {
    salvo = localStorage.getItem(CHAVE);
  } catch {
    /* navegador com armazenamento bloqueado — segue no padrão */
  }
  if (TEMAS.includes(salvo)) return salvo;
  try {
    if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      return "escuro";
    }
  } catch {
    /* sem matchMedia */
  }
  return "claro";
}

export function aplicarTema(tema) {
  const t = TEMAS.includes(tema) ? tema : "claro";
  document.documentElement.setAttribute("data-tema", t);
  try {
    localStorage.setItem(CHAVE, t);
  } catch {
    /* sem armazenamento: vale para esta sessão e pronto */
  }
  return t;
}

export function alternarTema() {
  return aplicarTema(temaAtual() === "escuro" ? "claro" : "escuro");
}

// Aplica no carregamento do módulo, antes de qualquer render.
aplicarTema(temaAtual());
