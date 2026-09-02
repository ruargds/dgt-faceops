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

// Períodos oferecidos. Não é campo de data livre de propósito: a
// pergunta real é quase sempre "hoje", "esta semana" ou "este mês", e
// dois calendários para responder isso é atrito sem retorno.
const PERIODOS = [
  { dias: 1, rotulo: "24 horas" },
  { dias: 7, rotulo: "7 dias" },
  { dias: 30, rotulo: "30 dias" },
  { dias: 90, rotulo: "90 dias" },
];

function Acoes() {
  const [lista, setLista] = useState([]);
  const [opcoes, setOpcoes] = useState({ usuarios: [], acoes: [] });
  const [busca, setBusca] = useState("");
  // A busca aplicada é separada da digitada: sem isso, cada tecla vira
  // uma consulta ao banco de auditoria.
  const [buscaAtiva, setBuscaAtiva] = useState("");
  const [nivel, setNivel] = useState("");
  const [usuario, setUsuario] = useState("");
  const [acao, setAcao] = useState("");
  const [dias, setDias] = useState(30);
  const [soFalhas, setSoFalhas] = useState(false);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);

  const LIMITE = 300;

  // Espera a digitação parar antes de consultar.
  useEffect(() => {
    const id = setTimeout(() => setBuscaAtiva(busca.trim()), 350);
    return () => clearTimeout(id);
  }, [busca]);

  // Monta a query uma vez e usa nos dois lugares — tela e exportação —
  // para não haver dois entendimentos do mesmo filtro.
  const filtros = useCallback((incluirLimite) => {
    const p = new URLSearchParams();
    if (buscaAtiva) p.set("busca", buscaAtiva);
    if (nivel) p.set("level", nivel);
    if (usuario) p.set("usuario", usuario);
    if (acao) p.set("action", acao);
    if (soFalhas) p.set("so_falhas", "true");
    if (incluirLimite) {
      p.set("desde", new Date(Date.now() - dias * 86400000).toISOString());
      p.set("limite", String(LIMITE));
    }
    const q = p.toString();
    return q ? `?${q}` : "";
  }, [buscaAtiva, nivel, usuario, acao, soFalhas, dias]);

  const carregar = useCallback(async () => {
    setErro("");
    setCarregando(true);
    try {
      setLista(await api.auditoria(filtros(true)));
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, [filtros]);

  useEffect(() => {
    carregar();
  }, [carregar]);

  // Quem e o quê existem no log — carregado uma vez, não a cada filtro.
  useEffect(() => {
    api.filtrosAuditoria(90).then(setOpcoes).catch(() => {});
  }, []);

  const filtrando = Boolean(buscaAtiva || nivel || usuario || acao || soFalhas);

  function limpar() {
    setBusca("");
    setBuscaAtiva("");
    setNivel("");
    setUsuario("");
    setAcao("");
    setSoFalhas(false);
    setDias(30);
  }

  return (
    <>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="filtros">
          <div className="filtro-busca">
            <input
              type="search"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              placeholder={t("Buscar por usuário, ação, alvo ou detalhe…")}
              aria-label={t("Buscar na auditoria")}
            />
          </div>

          <select value={dias} onChange={(e) => setDias(Number(e.target.value))}>
            {PERIODOS.map((p) => (
              <option key={p.dias} value={p.dias}>{t("Últimos")} {t(p.rotulo)}</option>
            ))}
          </select>

          <select value={nivel} onChange={(e) => setNivel(e.target.value)}>
            <option value="">{t("Todos os níveis")}</option>
            <option value="critical">{t("Só críticos")}</option>
            <option value="warning">{t("Só atenção")}</option>
            <option value="info">{t("Só info")}</option>
          </select>

          <select value={usuario} onChange={(e) => setUsuario(e.target.value)}>
            <option value="">{t("Todos os usuários")}</option>
            {opcoes.usuarios.map((u) => (
              <option key={u} value={u}>{u}</option>
            ))}
          </select>

          <select value={acao} onChange={(e) => setAcao(e.target.value)}>
            <option value="">{t("Todas as ações")}</option>
            {opcoes.acoes.map((a) => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>

          <label className="check" style={{ margin: 0, whiteSpace: "nowrap" }}>
            <input
              type="checkbox"
              checked={soFalhas}
              onChange={(e) => setSoFalhas(e.target.checked)}
            />
            <span>{t("Só o que falhou")}</span>
          </label>

          <div className="filtro-acoes">
            {filtrando && (
              <button className="btn btn-secondary btn-sm" onClick={limpar}>
                {t("Limpar filtros")}
              </button>
            )}
            <button className="btn btn-secondary btn-sm" onClick={carregar}>
              <IconAtualizar size={14} /> {t("Atualizar")}
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() =>
                api
                  .baixar(
                    api.urlExportarAuditoria(dias, filtros(false).replace("?", "&")),
                    `auditoria-${dias}d.csv`,
                  )
                  .catch(() => {})
              }
              title={t("Baixar em CSV exatamente o que está filtrado aqui")}
            >
              <IconDownload size={14} /> {t("Exportar")}
            </button>
          </div>
        </div>

        {/* Quantos registros, e o aviso de teto. Lista que para em 300
            sem dizer isso faz a pessoa concluir que não há mais nada. */}
        {!carregando && (
          <div className="small muted" style={{ marginTop: 10 }}>
            {lista.length === 0
              ? t("Nenhum registro com esses filtros.")
              : `${lista.length} ${t("registro(s)")}`}
            {lista.length >= LIMITE && (
              <>
                {" — "}
                <strong>{t("teto de")} {LIMITE} {t("atingido")}</strong>
                {": "}
                {t("refine a busca ou o período; a exportação traz mais.")}
              </>
            )}
            {filtrando && ` · ${t("filtros ativos")}`}
          </div>
        )}
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {carregando ? (
        <Carregando />
      ) : lista.length === 0 ? (
        <Vazio titulo={filtrando ? t("Nada encontrado") : t("Nenhum registro")}>
          {filtrando && (
            <div className="small muted" style={{ marginTop: 6 }}>
              {t("Nenhuma ação registrada corresponde a esses filtros. Tente limpar algum deles ou ampliar o período.")}
            </div>
          )}
        </Vazio>
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
