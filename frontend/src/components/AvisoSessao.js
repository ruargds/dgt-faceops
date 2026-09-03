import React from "react";
import { t } from "../i18n";
import { IconAlerta } from "./Icons";

/**
 * Aviso de que a sessão vai cair por inatividade.
 *
 * Existe porque encerrar sem avisar perde o que estava sendo digitado —
 * e quem perde um formulário preenchido pela metade passa a desconfiar
 * do painel inteiro, não só da regra de sessão.
 *
 * Fica sobre a tela e não fecha ao clicar fora, de propósito: é um aviso
 * com prazo, e fechar por engano devolveria a pessoa ao mesmo lugar
 * sessenta segundos antes de cair.
 */
export function AvisoSessao({ segundos, minutos, onContinuar }) {
  return (
    <div className="modal-bg" style={{ zIndex: 9000 }}>
      <div className="modal" style={{ maxWidth: 460 }}>
        <div className="modal-head">
          <div className="modal-title">{t("Sua sessão vai encerrar")}</div>
        </div>
        <div className="modal-body">
          <div
            className="card card-tight"
            style={{
              background: "var(--amber-bg)",
              borderColor: "var(--amber-bd)",
              marginBottom: 14,
            }}
          >
            <div className="stack-h" style={{ color: "var(--amber-fg)", alignItems: "flex-start" }}>
              <IconAlerta size={18} />
              <div style={{ flex: 1, fontSize: 13 }}>
                {t("Sem uso há quase")} {minutos} {t("minutos. A sessão encerra em")}{" "}
                <strong>{segundos}s</strong>.
              </div>
            </div>
          </div>
          <div className="small muted">
            {t("Continuar não estende o limite de 24 h, contado desde o login.")}
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn btn-primary" onClick={onContinuar} autoFocus>
            {t("Continuar conectado")}
          </button>
        </div>
      </div>
    </div>
  );
}
