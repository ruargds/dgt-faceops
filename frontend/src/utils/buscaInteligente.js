/*
 * Busca inteligente — a mesma régua do InfraCore, num lugar só.
 *
 * O contrato é idêntico ao de lá de propósito: quem usa os dois painéis
 * digita do mesmo jeito nos dois. Gêmea em `backend/app/core/busca.py`,
 * para a busca do servidor responder igual à da tela.
 *
 * | digitou | acha |
 * |---|---|
 * | `video` | o que COMEÇA uma palavra com "video" |
 * | `%video` | em qualquer parte, inclusive no meio |
 * | `"video"` | só a palavra inteira |
 * | `^video` | igual ao padrão, explícito |
 *
 * Começo de PALAVRA, não do campo: `worker` precisa achar
 * `findface-video-worker`. Vírgula, ponto-e-vírgula e quebra de linha
 * separam termos (OU entre eles); espaço não separa, para "Escola
 * Central" continuar valendo como um termo só.
 *
 * Acento não importa nos dois sentidos: `camera` acha "câmera".
 *
 * O que casa no meio da palavra não é escondido — é ranqueado abaixo
 * (`pontuacaoBusca`). Esconder resolve um incômodo e cria outro pior:
 * quem vê o item na tela e recebe "nenhum resultado" conclui que a busca
 * quebrou, sem ter como descobrir por quê.
 */

/** Minúsculas, sem acento, sem espaço nas pontas. */
export function normalizarTexto(valor) {
  return String(valor ?? "")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
}

/** Quebra o que foi digitado em termos. Vazio = não filtra. */
export function termosDaBusca(busca) {
  return String(busca ?? "")
    .split(/[,;\n]/)
    .map(normalizarTexto)
    .filter(Boolean);
}

/** Caractere de palavra — mesma definição do `\m` do Postgres. */
const PALAVRA = /[a-z0-9_]/;

/** Separa o operador do texto: `["contem"|"inicio"|"exato", texto]`. */
export function lerTermo(termo) {
  const t = String(termo ?? "").trim();
  if (!t) return [null, ""];
  if (t.length >= 2 && t[0] === '"' && t[t.length - 1] === '"') {
    return ["exato", t.slice(1, -1).trim()];
  }
  if (t[0] === "^") return ["inicio", t.slice(1).trim()];
  if (t[0] === "%") return ["contem", t.slice(1).trim()];
  return ["inicio", t];
}

function* posicoes(alvo, texto) {
  let i = alvo.indexOf(texto);
  while (i !== -1) {
    yield i;
    i = alvo.indexOf(texto, i + 1);
  }
}

/**
 * Um termo contra um alvo já normalizado.
 *
 * `indexOf` em laço, não expressão regular: o termo vem de quem digita e
 * viraria regex inválida com um `(` solto, ou varredura cara com `.*`.
 */
export function casaTermo(alvo, termo) {
  const [modo, texto] = lerTermo(termo);
  if (!texto) return true;
  if (modo === "contem") return alvo.includes(texto);
  for (const i of posicoes(alvo, texto)) {
    if (i !== 0 && PALAVRA.test(alvo[i - 1])) continue;
    if (modo === "exato") {
      const depois = i + texto.length;
      if (depois < alvo.length && PALAVRA.test(alvo[depois])) continue;
    }
    return true;
  }
  return false;
}

/** `true` se algum termo aparecer em algum valor. Sem termos, `true`. */
export function casaBusca(termos, ...valores) {
  if (!termos || termos.length === 0) return true;
  const alvo = valores.map(normalizarTexto).filter(Boolean).join(" | ");
  return termos.some((t) => casaTermo(alvo, t));
}

/**
 * Qualidade do casamento: 0 começa o campo, 1 começa uma palavra,
 * 2 casou no meio, 3 não casou. Serve para ordenar sem excluir.
 */
export function pontuacaoBusca(termos, ...valores) {
  if (!termos || termos.length === 0) return 0;
  const alvo = valores.map(normalizarTexto).filter(Boolean).join(" | ");
  let melhor = 3;
  for (const termo of termos) {
    const [, texto] = lerTermo(termo);
    if (!texto || !casaTermo(alvo, termo)) continue;
    for (const i of posicoes(alvo, texto)) {
      if (i === 0) return 0;
      melhor = Math.min(melhor, PALAVRA.test(alvo[i - 1]) ? 2 : 1);
    }
  }
  return melhor;
}

/**
 * Como `casaBusca`, mas número é identificador: casa só o número inteiro.
 *
 * Numa lista de 200 câmeras, procurar `12` por `includes` traz 112, 120 e
 * 212. Para código, o esperado é o número completo; para nome, segue por
 * trecho. Misturar funciona: `12, portaria`.
 */
export function casaBuscaExata(termos, ...valores) {
  if (!termos || termos.length === 0) return true;
  const alvo = valores.map(normalizarTexto).filter(Boolean).join(" | ");
  const numeros = new Set(alvo.match(/\d+/g) || []);
  return termos.some((t) => (/^\d+$/.test(t) ? numeros.has(t) : casaTermo(alvo, t)));
}

/**
 * Legenda dos campos de busca, num lugar só.
 *
 * @param escopo onde aquele campo procura — a única parte que muda de
 *   tela para tela, e a que responde "por que não achei?".
 */
export function ajudaDeBusca(escopo) {
  return [
    escopo,
    "",
    "video      começa com video",
    "%video     em qualquer parte da palavra",
    '"video"    só a palavra inteira',
    "",
    "Vírgula separa vários termos.",
    "Acento não importa: camera acha câmera.",
  ].join(String.fromCharCode(10));
}
