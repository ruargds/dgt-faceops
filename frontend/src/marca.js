/**
 * Identidade visual aplicada em tempo de execução.
 *
 * Sem isto, atender outro cliente exigiria trocar arquivo e reconstruir a
 * imagem. Aqui a mesma imagem serve todos: cores viram variáveis CSS no
 * :root, e o logo vem do backend quando existe.
 */
import { api } from "./api";

const PADRAO = {
  nome: "FaceOps",
  subtitulo: "Operação do FindFace Multi",
  cliente: "",
  cor_escura: "#0D1F35",
  cor_primaria: "#1A6FC4",
  cor_destaque: "#00AEEF",
};

/** Escurece um hexadecimal em `pct` por cento — usado no estado :hover. */
function escurecer(hex, pct) {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex).trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  const f = 1 - pct / 100;
  const r = Math.max(0, Math.round(((n >> 16) & 255) * f));
  const g = Math.max(0, Math.round(((n >> 8) & 255) * f));
  const b = Math.max(0, Math.round((n & 255) * f));
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

function valido(hex) {
  return /^#[0-9a-fA-F]{6}$/.test(String(hex || "").trim());
}

export function aplicar(marca) {
  const m = { ...PADRAO, ...(marca || {}) };
  const raiz = document.documentElement;

  // Só sobrescreve o que for hexadecimal válido. Valor digitado errado na
  // configuração não pode deixar a tela ilegível.
  if (valido(m.cor_escura)) {
    raiz.style.setProperty("--navy", m.cor_escura);
    raiz.style.setProperty("--navy-2", escurecer(m.cor_escura, -12));
  }
  if (valido(m.cor_primaria)) {
    raiz.style.setProperty("--blue", m.cor_primaria);
    raiz.style.setProperty("--blue-2", escurecer(m.cor_primaria, 12));
  }
  if (valido(m.cor_destaque)) {
    raiz.style.setProperty("--cyan", m.cor_destaque);
  }

  document.title = m.cliente ? `${m.nome} — ${m.cliente}` : m.nome;
  return m;
}

/** Busca a identidade e aplica. Falha em silêncio: o padrão já está no CSS. */
export async function carregarMarca() {
  try {
    const [cfg, logos] = await Promise.all([
      api.configPublico(),
      api.marcaSituacao().catch(() => ({})),
    ]);
    const m = aplicar(cfg);
    m.logos = logos || {};

    if (m.logos.favicon) {
      const link = document.querySelector("link[rel='icon']");
      if (link) link.href = `/api/marca/favicon?v=${Date.now()}`;
    }
    return m;
  } catch {
    return aplicar(null);
  }
}

/** Caminho do logo: o enviado, ou o padrão embutido. */
export function urlLogo(logos, tipo, padrao) {
  return logos && logos[tipo] ? `/api/marca/${tipo}` : padrao;
}

export const MARCA_PADRAO = PADRAO;
