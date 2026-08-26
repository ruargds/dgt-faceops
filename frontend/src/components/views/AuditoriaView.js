import React, { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatData, formatDuracao } from "../../api";
import { t } from "../../i18n";
import { usePermissions } from "../../usePermissions";
import { Carregando, Erro, Vazio } from "../Comuns";
import { IconAtualizar, IconDownload } from "../Icons";

const NIVEIS = {
  info: ["pill-idle", "info"],
  warning: ["pill-warn", "atenção"],
  critical: ["pill-err", "crítico"],
};

export default function AuditoriaView() {
  const { has } = usePermissions();
  const [aba, setAba] = useState("acoes");

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.auditoria")}</div>
          <div className="page-sub">
            {t("tela.auditoria.sub")}
          </div>
        </div>
        <div className="page-actions">
          <button
            className={`btn ${aba === "acoes" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setAba("acoes")}
          >{t("Ações")}</button>
          {has("terminal.sessions.view") && (
            <button
              className={`btn ${aba === "sessoes" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setAba("sessoes")}
            >{t("Sessões de terminal")}</button>
          )}
        </div>
      </div>

      {aba === "acoes" ? <Acoes /> : <Sessoes />}
    </>
  );
}

function Acoes() {
  const [lista, setLista] = useState([]);
  const [nivel, setNivel] = useState("");
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);

  const carregar = useCallback(async () => {
    setErro("");
    setCarregando(true);
    try {
      setLista(await api.auditoria(nivel ? `?level=${nivel}` : ""));
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, [nivel]);

  useEffect(() => {
    carregar();
  }, [carregar]);

  return (
    <>
      <div className="stack-h" style={{ marginBottom: 14 }}>
        <select value={nivel} onChange={(e) => setNivel(e.target.value)} style={{ width: "auto" }}>
          <option value="">{t("Todos os níveis")}</option>
          <option value="critical">{t("Só críticos")}</option>
          <option value="warning">{t("Só atenção")}</option>
          <option value="info">{t("Só info")}</option>
        </select>
        <button className="btn btn-secondary" onClick={carregar}>
          <IconAtualizar size={15} /> {t("Atualizar")}</button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() =>
            api.baixar(api.urlExportarAuditoria(90), "auditoria-90d.csv").catch(() => {})
          }
          title={t("Baixar 90 dias de auditoria em CSV")}
        >
          <IconDownload size={15} /> {t("Exportar 90 dias")}</button>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {carregando ? (
        <Carregando />
      ) : lista.length === 0 ? (
        <Vazio titulo={t("Nenhum registro")} />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("Quando")}</th>
                <th>{t("Usuário")}</th>
                <th>{t("Ação")}</th>
                <th>{t("Alvo")}</th>
                <th>{t("Nível")}</th>
                <th>{t("Detalhe")}</th>
              </tr>
            </thead>
            <tbody>
              {lista.map((r) => {
                const [classe, texto] = NIVEIS[r.level] || ["pill-idle", r.level];
                return (
                  <tr key={r.id}>
                    <td className="small">{formatData(r.ts)}</td>
                    <td className="mono small">
                      {r.usuario}
                      {r.ip && <div className="muted">{r.ip}</div>}
                    </td>
                    <td className="mono small">{r.action}</td>
                    <td className="mono small">{r.target || "—"}</td>
                    <td>
                      <span className={`pill ${classe}`}>{texto}</span>
                      {!r.success && (
                        <div className="small" style={{ color: "var(--red)", marginTop: 3 }}>
                          falhou
                        </div>
                      )}
                    </td>
                    <td className="small muted" style={{ maxWidth: 340 }}>
                      {Object.keys(r.detail || {}).length > 0 ? (
                        <span className="mono" style={{ fontSize: 11.5 }}>
                          {JSON.stringify(r.detail).slice(0, 200)}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function Sessoes() {
  const [historico, setHistorico] = useState([]);
  const [ativas, setAtivas] = useState([]);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);

  const carregar = useCallback(async () => {
    setErro("");
    try {
      const [h, a] = await Promise.all([
        api.sessoesTerminal(),
        api.sessoesAtivas().catch(() => []),
      ]);
      setHistorico(h);
      setAtivas(a);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  if (carregando) return <Carregando />;

  return (
    <div className="stack-v">
      <Erro mensagem={erro} onTentar={carregar} />

      <div className="stack-h">
        <button className="btn btn-secondary" onClick={carregar}>
          <IconAtualizar size={15} /> {t("Atualizar")}</button>
        {ativas.length > 0 && (
          <span className="pill pill-info">{ativas.length} sessão(ões) aberta(s) agora</span>
        )}
      </div>

      {ativas.length > 0 && (
        <div className="card">
          <div className="section-title">{t("Abertas neste momento")}</div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("Usuário")}</th>
                  <th>{t("Servidor")}</th>
                  <th>IP</th>
                  <th className="right">{t("Parada há")}</th>
                  <th className="right">{t("Tráfego")}</th>
                </tr>
              </thead>
              <tbody>
                {ativas.map((s) => (
                  <tr key={s.chave}>
                    <td className="mono small">{s.usuario}</td>
                    <td className="mono small">{s.host}</td>
                    <td className="mono small muted">{s.ip}</td>
                    <td className="right small">{formatDuracao(s.ocioso_s)}</td>
                    <td className="right small mono">
                      ↑ {formatBytes(s.bytes_in)} · ↓ {formatBytes(s.bytes_out)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div>
        <div className="section-title">{t("Histórico de sessões")}</div>
        {historico.length === 0 ? (
          <Vazio titulo={t("Nenhuma sessão de terminal registrada")} />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("Início")}</th>
                  <th>{t("Usuário")}</th>
                  <th>{t("Servidor")}</th>
                  <th>{t("Duração")}</th>
                  <th>sudo</th>
                  <th className="right">{t("Tráfego")}</th>
                  <th>{t("Encerrou por")}</th>
                  <th style={{ width: 1 }}></th>
                </tr>
              </thead>
              <tbody>
                {historico.map((s) => {
                  const dur =
                    s.ended_at && s.started_at
                      ? (new Date(s.ended_at) - new Date(s.started_at)) / 1000
                      : null;
                  return (
                    <tr key={s.id}>
                      <td className="small">{formatData(s.started_at)}</td>
                      <td className="mono small">{s.usuario}</td>
                      <td className="mono small">{s.host_nome}</td>
                      <td className="small">
                        {dur != null ? formatDuracao(dur) : <span className="pill pill-info">aberta</span>}
                      </td>
                      <td>
                        {s.sudo_used ? (
                          <span className="pill pill-warn">usou</span>
                        ) : (
                          <span className="muted small">—</span>
                        )}
                      </td>
                      <td className="right small mono">
                        ↑ {formatBytes(s.bytes_in)} · ↓ {formatBytes(s.bytes_out)}
                      </td>
                      <td className="small muted">{s.end_reason || "—"}</td>
                      <td>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() =>
                            api.baixar(api.urlGravacao(s.id), `sessao-${s.id}.cast`).catch(() => {})
                          }
                          title="Baixar gravação (asciicast v2)"
                        >
                          <IconDownload size={13} />
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div className="small muted" style={{ marginTop: 8 }}>
          Reproduzir uma gravação: <span className="mono">{t("asciinema play arquivo.cast")}</span>
        </div>
      </div>
    </div>
  );
}
