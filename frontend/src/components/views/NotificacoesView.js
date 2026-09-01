import React, { useCallback, useEffect, useState } from "react";
import { api, formatData } from "../../api";
import { t } from "../../i18n";
import { Carregando, Erro, Vazio } from "../Comuns";
import { IconAlerta, IconAtualizar, IconLixeira, IconOk } from "../Icons";

/**
 * Aviso por Telegram — um bot por cliente, um grupo de destino.
 *
 * Mesmo fluxo que a equipe já usa no Zabbix: cria-se um bot para o
 * cliente, adiciona-se ao grupo de quem precisa receber, e configura-se
 * aqui de quais servidores e serviços vêm os avisos.
 *
 * O token é escrito, nunca lido: depois de salvo, a tela mostra o nome do
 * bot e a impressão digital. Para trocar de conta, basta colar um token
 * novo — o antigo é substituído.
 */
const NIVEL_ROTULO = {
  critico: "Só quando parar",
  atencao: "Atenção e parada",
};

export default function NotificacoesView() {
  const [conta, setConta] = useState(null);
  const [regras, setRegras] = useState(null);
  const [hosts, setHosts] = useState([]);
  const [envios, setEnvios] = useState(null);
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [salvando, setSalvando] = useState(false);
  const [expandido, setExpandido] = useState({});

  const [form, setForm] = useState({ bot_token: "", chat_id: "", ativo: true });

  const carregar = useCallback(async () => {
    setErro("");
    try {
      const [c, r] = await Promise.all([api.notifConta(), api.notifRegras()]);
      setConta(c);
      setRegras(r.regras);
      setHosts(r.hosts);
      setForm((f) => ({ ...f, chat_id: c.chat_id || "", ativo: c.ativo }));
    } catch (ex) {
      setErro(ex.message);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function salvarConta(e) {
    e.preventDefault();
    setSalvando(true);
    setErro("");
    setAviso("");
    try {
      const c = await api.salvarNotifConta(form);
      setConta(c);
      setForm((f) => ({ ...f, bot_token: "" }));
      setAviso(t("Conta salva."));
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setSalvando(false);
    }
  }

  async function testar() {
    setErro("");
    setAviso("");
    try {
      await api.testarNotif();
      setAviso(t("Mensagem de teste enviada — confira o grupo."));
    } catch (ex) {
      setErro(ex.message);
    }
  }

  // Uma regra por (host, serviço). Serviço vazio = todos daquele host.
  function regraDe(hostId, servico = "") {
    return (regras || []).find(
      (r) => r.host_id === hostId && r.servico === servico
    );
  }

  async function alternarRegra(hostId, servico, ligar, extras = {}) {
    setErro("");
    try {
      if (!ligar) {
        const atual = regraDe(hostId, servico);
        if (atual) await api.removerNotifRegra(atual.id);
      } else {
        const atual = regraDe(hostId, servico);
        await api.salvarNotifRegra({
          host_id: hostId,
          servico,
          nivel_minimo: extras.nivel_minimo || (atual && atual.nivel_minimo) || "critico",
          avisar_retorno:
            extras.avisar_retorno !== undefined
              ? extras.avisar_retorno
              : atual
              ? atual.avisar_retorno
              : true,
          ativo: true,
        });
      }
      const r = await api.notifRegras();
      setRegras(r.regras);
      setHosts(r.hosts);
    } catch (ex) {
      setErro(ex.message);
    }
  }

  async function verEnvios() {
    try {
      const r = await api.notifEnvios(20);
      setEnvios(r.envios);
    } catch (ex) {
      setErro(ex.message);
    }
  }

  if (!conta && !erro) return <Carregando />;

  const regraGeral = regraDe(null, "");

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("Avisos no Telegram")}</div>
          <div className="page-sub">
            {t("Um bot para este cliente, mandando para o grupo de quem precisa receber")}
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={carregar}>
            <IconAtualizar size={15} /> {t("Recarregar")}
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {aviso && (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 14 }}
        >
          <span className="small" style={{ color: "var(--green-fg)" }}>
            <IconOk size={13} /> {aviso}
          </span>
        </div>
      )}

      {/* ── Conta ──────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>{t("Conta de envio")}</div>
        <div className="small muted" style={{ marginBottom: 14 }}>
          {t("Crie o bot no @BotFather, adicione ao grupo e cole aqui o token. O id do grupo começa com hífen. O token é guardado cifrado e nunca é exibido de volta.")}
        </div>

        {conta && conta.configurado && (
          <div className="stack-h small" style={{ gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
            <span className={`pill ${conta.ativo ? "pill-ok" : "pill-idle"}`}>
              {conta.ativo ? t("ativo") : t("desligado")}
            </span>
            <span>
              <strong>{conta.bot_nome ? `@${conta.bot_nome}` : t("bot")}</strong>
            </span>
            <span className="muted mono">{t("token")} {conta.token_fingerprint}</span>
            <span className="muted">
              {t("por")} {conta.atualizado_por} · {formatData(conta.atualizado_em)}
            </span>
          </div>
        )}

        <form className="row row-3" onSubmit={salvarConta} style={{ alignItems: "flex-end" }}>
          <div className="field">
            <label className="label">
              {conta && conta.configurado ? t("Trocar token do bot") : t("Token do bot")}
            </label>
            <input
              type="password"
              className="mono"
              autoComplete="new-password"
              placeholder={conta && conta.configurado ? t("deixe vazio para manter") : "123456:ABC-DEF..."}
              value={form.bot_token}
              onChange={(e) => setForm({ ...form, bot_token: e.target.value })}
            />
            <div className="field-help">{t("Validado no Telegram antes de salvar.")}</div>
          </div>
          <div className="field">
            <label className="label label-required">{t("Id do grupo")}</label>
            <input
              className="mono"
              placeholder="-1001234567890"
              value={form.chat_id}
              onChange={(e) => setForm({ ...form, chat_id: e.target.value })}
              required
            />
          </div>
          <div className="field">
            <label className="check" style={{ marginBottom: 10 }}>
              <input
                type="checkbox"
                checked={form.ativo}
                onChange={(e) => setForm({ ...form, ativo: e.target.checked })}
              />
              <span>{t("Enviar avisos")}</span>
            </label>
            <div className="stack-h" style={{ gap: 6 }}>
              <button className="btn btn-primary" disabled={salvando}>
                {salvando ? t("Salvando…") : t("Salvar conta")}
              </button>
              {conta && conta.configurado && (
                <button type="button" className="btn btn-secondary" onClick={testar}>
                  {t("Enviar teste")}
                </button>
              )}
            </div>
          </div>
        </form>
      </div>

      {/* ── O que receber ──────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>{t("O que receber")}</div>
        <div className="small muted" style={{ marginBottom: 14 }}>
          {t("Sem nenhuma regra ligada, nada é enviado — silêncio é o padrão. A regra mais específica vence: serviço, depois servidor, depois a regra geral.")}
        </div>

        {/* Permitir todos */}
        <div
          className="card card-tight"
          style={{ marginBottom: 14, borderColor: regraGeral ? "var(--blue)" : "var(--border)" }}
        >
          <div className="stack-h" style={{ justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
            <label className="check" style={{ margin: 0 }}>
              <input
                type="checkbox"
                checked={Boolean(regraGeral)}
                onChange={(e) => alternarRegra(null, "", e.target.checked)}
              />
              <span>
                <strong>{t("Todos os servidores e serviços")}</strong>
                <br />
                <span className="muted">{t("Vale para o que não tiver regra própria abaixo")}</span>
              </span>
            </label>
            {regraGeral && (
              <div className="stack-h" style={{ gap: 8 }}>
                <select
                  value={regraGeral.nivel_minimo}
                  onChange={(e) => alternarRegra(null, "", true, { nivel_minimo: e.target.value })}
                  style={{ width: "auto" }}
                >
                  {Object.entries(NIVEL_ROTULO).map(([v, r]) => (
                    <option key={v} value={v}>{t(r)}</option>
                  ))}
                </select>
                <label className="check" style={{ margin: 0 }}>
                  <input
                    type="checkbox"
                    checked={regraGeral.avisar_retorno}
                    onChange={(e) =>
                      alternarRegra(null, "", true, { avisar_retorno: e.target.checked })
                    }
                  />
                  <span>{t("avisar quando voltar")}</span>
                </label>
              </div>
            )}
          </div>
        </div>

        {/* Por servidor */}
        {hosts.length === 0 ? (
          <Vazio titulo={t("Nenhum servidor ativo")}>
            {t("Cadastre servidores para escolher de quais receber aviso.")}
          </Vazio>
        ) : (
          <div className="stack-v" style={{ gap: 10 }}>
            {hosts.map((h) => {
              const regraHost = regraDe(h.id, "");
              const aberto = expandido[h.id];
              const servicosComRegra = (regras || []).filter(
                (r) => r.host_id === h.id && r.servico
              );
              return (
                <div key={h.id} className="card card-tight">
                  <div className="stack-h" style={{ justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                    <label className="check" style={{ margin: 0 }}>
                      <input
                        type="checkbox"
                        checked={Boolean(regraHost)}
                        onChange={(e) => alternarRegra(h.id, "", e.target.checked)}
                      />
                      <span>
                        <strong>{h.nome}</strong>
                        <span className="muted"> · {h.servicos.length} {t("serviços conhecidos")}</span>
                        {servicosComRegra.length > 0 && (
                          <span className="pill pill-info" style={{ marginLeft: 8 }}>
                            {servicosComRegra.length} {t("regra(s) por serviço")}
                          </span>
                        )}
                      </span>
                    </label>

                    <div className="stack-h" style={{ gap: 8 }}>
                      {regraHost && (
                        <>
                          <select
                            value={regraHost.nivel_minimo}
                            onChange={(e) =>
                              alternarRegra(h.id, "", true, { nivel_minimo: e.target.value })
                            }
                            style={{ width: "auto" }}
                          >
                            {Object.entries(NIVEL_ROTULO).map(([v, r]) => (
                              <option key={v} value={v}>{t(r)}</option>
                            ))}
                          </select>
                          <label className="check" style={{ margin: 0 }}>
                            <input
                              type="checkbox"
                              checked={regraHost.avisar_retorno}
                              onChange={(e) =>
                                alternarRegra(h.id, "", true, { avisar_retorno: e.target.checked })
                              }
                            />
                            <span>{t("retorno")}</span>
                          </label>
                        </>
                      )}
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => setExpandido((x) => ({ ...x, [h.id]: !x[h.id] }))}
                        disabled={h.servicos.length === 0}
                        title={h.servicos.length === 0 ? t("O coletor ainda não viu os serviços deste servidor") : ""}
                      >
                        {aberto ? t("esconder serviços") : t("escolher serviço a serviço")}
                      </button>
                    </div>
                  </div>

                  {aberto && (
                    <div
                      style={{
                        marginTop: 12, paddingTop: 12,
                        borderTop: "1px solid var(--border)",
                        display: "grid",
                        gap: 6,
                        gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
                      }}
                    >
                      {h.servicos.map((s) => {
                        const r = regraDe(h.id, s);
                        return (
                          <label key={s} className="check" style={{ margin: 0 }}>
                            <input
                              type="checkbox"
                              checked={Boolean(r)}
                              onChange={(e) => alternarRegra(h.id, s, e.target.checked)}
                            />
                            <span className="mono" style={{ fontSize: 12.5 }}>{s}</span>
                          </label>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Como chega ─────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>{t("Como a mensagem chega")}</div>
        <div className="small muted" style={{ marginBottom: 10 }}>
          {t("Curta de propósito: quem está de plantão decide pela prévia do celular, sem abrir o app.")}
        </div>
        <div className="row row-2">
          <pre className="mono small" style={{
            background: "var(--bg-2)", padding: 12, borderRadius: "var(--radius)",
            margin: 0, whiteSpace: "pre-wrap",
          }}>
{`🔴 PARADO · vm-appserver
findface-video-worker com problema
Provável: reiniciou 7x nos últimos 30 min
Desde 01/09 14:32`}
          </pre>
          <pre className="mono small" style={{
            background: "var(--bg-2)", padding: 12, borderRadius: "var(--radius)",
            margin: 0, whiteSpace: "pre-wrap",
          }}>
{`🟢 NORMALIZADO · vm-appserver
findface-video-worker voltou
Ficou fora 6min`}
          </pre>
        </div>
      </div>

      {/* ── Últimos envios ─────────────────────────────────────────── */}
      <div className="card">
        <div className="stack-h" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title" style={{ marginBottom: 4 }}>{t("Últimos envios")}</div>
            <div className="small muted">
              {t("É a resposta para 'não recebi'. Guardado por 14 dias e apagado pela faxina.")}
            </div>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={verEnvios}>
            {t("ver envios")}
          </button>
        </div>

        {envios && (
          envios.length === 0 ? (
            <div className="small muted" style={{ marginTop: 12 }}>
              {t("Nada enviado ainda.")}
            </div>
          ) : (
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("Quando")}</th>
                    <th>{t("Mensagem")}</th>
                    <th>{t("Situação")}</th>
                  </tr>
                </thead>
                <tbody>
                  {envios.map((e) => (
                    <tr key={e.id}>
                      <td className="small">{formatData(e.ts)}</td>
                      <td className="small mono" style={{ whiteSpace: "pre-wrap" }}>{e.texto}</td>
                      <td>
                        {e.status === "enviado" ? (
                          <span className="pill pill-ok">{t("enviado")}</span>
                        ) : (
                          <span className="pill pill-err" title={e.erro}>
                            <IconAlerta size={12} /> {t("falhou")}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </div>
    </>
  );
}
