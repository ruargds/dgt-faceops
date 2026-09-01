/**
 * Autoria e direitos do sistema — painel que só abre de propósito.
 *
 * Mesmo padrão do InfraCore (`components/SobreOSistema.js` de lá): o
 * rodapé visível pertence à marca de quem USA o painel (o cliente, via
 * `marca.js`); a empresa que DESENVOLVEU fica a cinco cliques dali. A
 * informação de autoria não é consultada no dia a dia — é consultada
 * quando alguém precisa dela: uma dúvida de licença, uma auditoria, uma
 * cópia encontrada em outro lugar.
 *
 * O gesto (cinco cliques em 3s) não é segurança, é discrição. A proteção
 * real de autoria está em `AUTORIA` viajar dentro do bundle e no
 * repositório, não neste painel.
 */
import React, { useEffect, useState } from "react";

export const AUTORIA = {
  // "FaceOps", sem misturar com a marca do cliente: o `<title>`, o
  // logo e o resto da interface seguem com a identidade de quem usa —
  // ver marca.js. Aqui é sempre a identidade do produto.
  produto: "FaceOps",
  desenvolvedora: "FIXR SERVIÇOS DE TECNOLOGIA LTDA",
  cnpj: "54.898.541/0001-08",
  desde: 2026,
};

export const ANO_ATUAL = new Date().getFullYear();

export const MARCA =
  `${AUTORIA.produto} — © ${AUTORIA.desde}-${ANO_ATUAL} ` +
  `${AUTORIA.desenvolvedora} (CNPJ ${AUTORIA.cnpj}). Software proprietário.`;

const CLIQUES = 5;
const JANELA_MS = 3000;

/**
 * Envolve um trecho de rodapé e conta os cliques. Use assim:
 *
 *     <GatilhoDeAutoria>© DGT Tecnologia em Segurança</GatilhoDeAutoria>
 */
export function GatilhoDeAutoria({ children, style }) {
  const [contagem, setContagem] = useState(0);
  const [aberto, setAberto] = useState(false);

  useEffect(() => {
    if (!contagem) return undefined;
    const t = setTimeout(() => setContagem(0), JANELA_MS);
    return () => clearTimeout(t);
  }, [contagem]);

  function clicou() {
    setContagem((n) => {
      if (n + 1 >= CLIQUES) {
        setAberto(true);
        return 0;
      }
      return n + 1;
    });
  }

  return (
    <>
      <div onClick={clicou} style={{ ...style, cursor: "default", userSelect: "none" }}>
        {children}
      </div>
      {aberto && <PainelDeAutoria onFechar={() => setAberto(false)} />}
    </>
  );
}

function Linha({ rotulo, children }) {
  return (
    <div style={{ display: "flex", gap: "10px", padding: "7px 0",
                  borderBottom: "1px solid var(--border)", fontSize: "13px" }}>
      <div style={{ minWidth: "132px", color: "var(--text-3)" }}>{rotulo}</div>
      <div style={{ color: "var(--titulo)" }}>{children}</div>
    </div>
  );
}

export function PainelDeAutoria({ onFechar }) {
  useEffect(() => {
    const aoTeclar = (e) => { if (e.key === "Escape") onFechar(); };
    window.addEventListener("keydown", aoTeclar);
    return () => window.removeEventListener("keydown", aoTeclar);
  }, [onFechar]);

  return (
    <div onClick={onFechar} style={{
      position: "fixed", inset: 0, background: "rgba(13,31,53,0.55)",
      display: "flex", alignItems: "center", justifyContent: "center",
      padding: "20px", zIndex: 9999,
    }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        background: "var(--white)", borderRadius: "14px", padding: "26px 26px 22px",
        width: "100%", maxWidth: "460px",
        boxShadow: "0 18px 48px rgba(13,31,53,0.28)",
        border: "1px solid var(--border)",
      }}>
        <h2 style={{ margin: "0 0 4px", fontSize: "17px", color: "var(--titulo)" }}>
          {AUTORIA.produto}
        </h2>
        <p style={{ margin: "0 0 16px", fontSize: "12.5px", color: "var(--text-3)" }}>
          Operação do FindFace Multi
        </p>

        <Linha rotulo="Desenvolvido por">{AUTORIA.desenvolvedora}</Linha>
        <Linha rotulo="CNPJ">{AUTORIA.cnpj}</Linha>
        <Linha rotulo="Direitos">
          © {AUTORIA.desde}{ANO_ATUAL > AUTORIA.desde ? `–${ANO_ATUAL}` : ""} — todos os
          direitos reservados
        </Linha>
        <Linha rotulo="Versão">
          {process.env.REACT_APP_BUILD_STAMP || "desenvolvimento"}
        </Linha>

        <p style={{ margin: "16px 0 0", fontSize: "12px", color: "var(--text-2)",
                    lineHeight: 1.6 }}>
          Software proprietário. O código-fonte, a estrutura de dados e as
          integrações são de propriedade da desenvolvedora; reprodução,
          distribuição ou derivação exigem autorização por escrito.
        </p>

        <button type="button" onClick={onFechar} className="btn btn-primary" style={{
          width: "100%", marginTop: "18px", justifyContent: "center", padding: "11px",
        }}>
          Fechar
        </button>
      </div>
    </div>
  );
}

export default PainelDeAutoria;
