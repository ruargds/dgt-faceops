import React, { useCallback, useEffect, useState } from "react";
import { api, formatData, formatDuracao } from "../../api";
import { t } from "../../i18n";
import { Carregando, Erro, Estatistica, Vazio } from "../Comuns";
import { IconAlerta, IconAtualizar, IconLixeira, IconLogs, IconOk } from "../Icons";

/**
 * Diagnóstico — o que repete, o que o log diz, e o que fazer.
 *
 * Três perguntas que o painel não respondia:
 *
 * 1. **O que repete?** Um serviço que cai uma vez é incidente; um que cai
 *    sete vezes em cinco dias, sempre de madrugada, é outra conversa.
 * 2. **O que o log está dizendo?** Erros agrupados por molde — mil linhas
 *    iguais viram uma com contador.
 * 3. **O que fazer?** Quando o erro é conhecido, vem com a causa e a tela
 *    que resolve, escritas por quem operou a máquina.
 *
 * Nenhum modelo de linguagem envolvido, de propósito: ver o cabeçalho de
 * `catalogo_erros.py`.
 */
const JANELAS = [7, 14, 30];

const TENDENCIA = {
  piorando: { rotulo: "piorando", cor: "var(--red)" },
  melhorando: { rotulo: "melhorando", cor: "var(--green)" },
  estavel: { rotulo: "estável", cor: "var(--text-3)" },
};

export default function DiagnosticoView({ nav }) {
  const [dias, setDias] = useState(14);
  const [reincidencia, setReincidencia] = useState(null);
  // O que a apuração descobriu nas quedas recentes. Esta tela mostrava
  // três zeros no dia seguinte a um incidente real: reincidência exige
  // três ocorrências, e molde de log só é lido de SERVIÇO com incidente
  // aberto — queda de máquina inteira, a mais grave, não entrava em
  // nenhum dos dois.
  const [apuracoes, setApuracoes] = useState(null);
  const [atualizadoEm, setAtualizadoEm] = useState(null);
  const [buscando, setBuscando] = useState(false);
  const [padroes, setPadroes] = useState(null);
  const [catalogo, setCatalogo] = useState(null);
  const [verCatalogo, setVerCatalogo] = useState(false);
  const [erro, setErro] = useState("");
  const [analisando, setAnalisando] = useState("");
  const [limpando, setLimpando] = useState(false);

  const carregar = useCallback(async () => {
    setErro("");
    setBuscando(true);
    try {
      const [r, p, a] = await Promise.all([
        api.reincidencia(dias),
        api.padroesLog(dias),
        api.apuracoesRecentes(dias).catch(() => ({ itens: [] })),
      ]);
      setReincidencia(r.itens);
      setPadroes(p.itens);
      setApuracoes(a.itens || []);
      setAtualizadoEm(new Date());
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setBuscando(false);
    }
  }, [dias]);

  useEffect(() => {
    carregar();
  }, [carregar]);

  useEffect(() => {
    if (!verCatalogo || catalogo) return;
    api.catalogoErros().then((r) => setCatalogo(r.itens)).catch(() => setCatalogo([]));
  }, [verCatalogo, catalogo]);

  // Zera os padrões coletados para recomeçar depois de resolver a causa.
  // Só o log agrupado sai; incidente e histórico de queda ficam.
  async function limparPadroes() {
    if (!window.confirm(t("Apagar os padrões de log coletados e recomeçar a contagem?"))) return;
    setLimpando(true);
    setErro("");
    try {
      await api.limparPadroesLog();
      setPadroes([]);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setLimpando(false);
    }
  }

  // Leitura de log no servidor — só no clique, nunca sozinha.
  async function analisarAgora(hostId, servico) {
    setAnalisando(`${hostId}:${servico}`);
    setErro("");
    try {
      const r = await api.analisarLog(hostId, servico);
      setPadroes(r.itens);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setAnalisando("");
    }
  }

  if (!reincidencia && !erro) return <Carregando texto={t("Cruzando incidentes e log…")} />;

  const recorrentes = reincidencia || [];
  const erros = (padroes || []).filter((p) => p.nivel === "erro");
  const conhecidos = (padroes || []).filter((p) => p.conhecido);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("Diagnóstico")}</div>
          <div className="page-sub">
            {t("O que repete, o que o log está dizendo e o que já se sabe sobre isso")}
          </div>
        </div>
        <div className="page-actions">
          {JANELAS.map((d) => (
            <button
              key={d}
              className={`btn btn-sm ${dias === d ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setDias(d)}
            >
              {d} {t("dias")}
            </button>
          ))}
          <button className="btn btn-secondary" onClick={carregar}>
            <IconAtualizar size={15} /> {buscando ? t("Atualizando…") : t("Atualizar")}
          </button>
          {/* Recomeçar: o contador acumulado de um erro já resolvido só
              atrapalha a leitura. Não toca em incidente. */}
          <button
            className="btn btn-danger"
            onClick={limparPadroes}
            disabled={limpando || !(padroes || []).length}
            title={t("Apaga os padrões de log coletados e recomeça a contagem")}
          >
            <IconLixeira size={14} /> {limpando ? t("limpando…") : t("Limpar log")}
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      <div className="grid-stats" style={{ marginBottom: 16 }}>
        <Estatistica
          rotulo={t("Problemas que repetem")}
          valor={recorrentes.length}
          sub={`${t("na janela de")} ${dias} ${t("dias")}`}
        />
        <Estatistica
          rotulo={t("Tipos de erro no log")}
          valor={erros.length}
          sub={t("agrupados por molde, não por linha")}
        />
        <Estatistica
          rotulo={t("Erros já conhecidos")}
          valor={conhecidos.length}
          sub={t("com causa e ação registradas")}
        />
      </div>

      {/* ── Reincidência ───────────────────────────────────────────── */}
      {/* ── O que foi apurado ──────────────────────────────────────
          Primeiro card de propósito: é a resposta mais direta a "o que
          aconteceu", e é a que faltava. */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="stack-h" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <div>
            <div className="section-title" style={{ marginBottom: 4 }}>
              {t("O que foi apurado nas quedas")}
            </div>
            <div className="small muted">
              {t("Lido no servidor no instante em que ele voltou — inclusive queda de máquina inteira, que não deixa molde de log.")}
            </div>
          </div>
          {atualizadoEm && (
            <span className="small muted">
              {t("atualizado às")}{" "}
              {atualizadoEm.toLocaleTimeString([], {
                hour: "2-digit", minute: "2-digit", second: "2-digit",
              })}
            </span>
          )}
        </div>

        <div style={{ marginTop: 12 }}>
          {apuracoes === null ? (
            <Carregando />
          ) : apuracoes.length === 0 ? (
            <div className="small muted">
              {t("Nenhuma queda apurada na janela. A apuração roda quando o incidente FECHA — incidente ainda aberto não aparece aqui.")}
            </div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("Quando")}</th>
                    <th>{t("Onde")}</th>
                    <th className="right">{t("Fora")}</th>
                    <th>{t("Causa apurada")}</th>
                  </tr>
                </thead>
                <tbody>
                  {apuracoes.map((a) => (
                    <tr key={a.id}>
                      <td className="small">{formatData(a.inicio)}</td>
                      <td className="small">
                        <strong>{a.host}</strong>
                        <div className="muted mono" style={{ fontSize: 11.5 }}>
                          {a.servico || t("máquina inteira")}
                        </div>
                      </td>
                      <td className="right mono small">{formatDuracao(a.duracao_s)}</td>
                      <td className="small">
                        <span
                          style={{
                            color:
                              a.confianca === "alta"
                                ? "var(--green-fg)"
                                : a.confianca === "media"
                                  ? "var(--amber-fg)"
                                  : "var(--text-2)",
                          }}
                        >
                          {a.veredito}
                        </span>
                        {a.evidencia && (
                          <div className="muted" style={{ marginTop: 2 }}>{a.evidencia}</div>
                        )}
                        {a.achados > 1 && (
                          <div className="muted" style={{ marginTop: 2, fontSize: 11.5 }}>
                            +{a.achados - 1} {t("linha(s) lidas — veja em Serviços › Histórico")}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          {t("O que repete")}
        </div>
        <div className="small muted" style={{ marginBottom: 12 }}>
          {t("Contagem sobre o histórico de indisponibilidade. Cair uma vez é incidente; cair sempre é causa não resolvida.")}
        </div>

        {recorrentes.length === 0 ? (
          <div className="stack-h small" style={{ color: "var(--green-fg)" }}>
            <IconOk size={15} />
            {t("Nada repetiu o suficiente para virar padrão nesta janela.")}
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("Serviço")}</th>
                  <th>{t("Servidor")}</th>
                  <th>{t("Quedas")}</th>
                  <th>{t("Tempo fora")}</th>
                  <th>{t("Horário típico")}</th>
                  <th>{t("Intervalo médio")}</th>
                  <th>{t("Tendência")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {recorrentes.map((r) => {
                  const tend = TENDENCIA[r.tendencia] || TENDENCIA.estavel;
                  const chave = `${r.host_id}:${r.servico}`;
                  return (
                    <tr key={`${chave}-${r.tipo}`}>
                      <td className="mono">
                        {r.servico || t("máquina inteira")}
                        {r.aberto_agora && (
                          <span className="pill pill-err" style={{ marginLeft: 6 }}>
                            {t("fora agora")}
                          </span>
                        )}
                      </td>
                      <td>{r.host}</td>
                      <td><strong>{r.ocorrencias}</strong></td>
                      <td>{formatDuracao(r.tempo_fora_s)}</td>
                      <td>
                        {r.hora_tipica === null
                          ? <span className="muted">{t("espalhado")}</span>
                          : `~${String(r.hora_tipica).padStart(2, "0")}h`}
                      </td>
                      <td>{r.intervalo_medio_h ? `${r.intervalo_medio_h} h` : "—"}</td>
                      <td style={{ color: tend.cor }}>{t(tend.rotulo)}</td>
                      <td>
                        <div className="stack-h" style={{ gap: 4 }}>
                          {r.servico && (
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              disabled={analisando === chave}
                              onClick={() => analisarAgora(r.host_id, r.servico)}
                              title={t("Lê o log deste container no servidor, agora")}
                            >
                              <IconLogs size={13} />
                              {analisando === chave ? t("lendo…") : t("analisar log")}
                            </button>
                          )}
                          {nav && (
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              onClick={() => nav("servicos", { hostId: r.host_id, servico: r.servico })}
                            >
                              {t("ir para Serviços")}
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Padrões de log ─────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          {t("O que o log está dizendo")}
        </div>
        <div className="small muted" style={{ marginBottom: 12 }}>
          {t("Erros agrupados por molde — id, hora, IP e número viram marcador, então mil linhas iguais viram uma com contador. Lido só de serviço com incidente aberto, ou no botão acima.")}
        </div>

        {!padroes ? (
          <Carregando />
        ) : padroes.length === 0 ? (
          <Vazio titulo={t("Nenhum padrão de erro registrado")}>
            {t("O painel lê o log quando um serviço entra em problema. Enquanto está tudo de pé, não lê nada — use")} <strong>{t("analisar log")}</strong> {t("acima para forçar uma leitura.")}
          </Vazio>
        ) : (
          <div className="stack-v" style={{ gap: 10 }}>
            {padroes.map((p) => (
              <div
                key={p.id}
                className="card card-tight"
                style={{
                  borderLeftWidth: 4,
                  borderLeftColor: p.nivel === "erro" ? "var(--red)" : "var(--amber)",
                }}
              >
                <div className="stack-h" style={{ justifyContent: "space-between", gap: 10 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="stack-h" style={{ gap: 6, marginBottom: 4 }}>
                      <span className={`pill ${p.nivel === "erro" ? "pill-err" : "pill-warn"}`}>
                        {p.ocorrencias}×
                      </span>
                      <strong className="mono small">{p.servico}</strong>
                      <span className="small muted">{p.host}</span>
                    </div>
                    <div className="small mono" style={{ wordBreak: "break-word", opacity: 0.9 }}>
                      {p.exemplo}
                    </div>
                    <div className="small muted" style={{ marginTop: 4 }}>
                      {t("primeira vez")} {formatData(p.primeira_vez)} · {t("última")} {formatData(p.ultima_vez)}
                    </div>
                  </div>
                </div>

                {p.conhecido && (
                  <div
                    className="card card-tight"
                    style={{ marginTop: 10, background: "var(--blue-bg)", borderColor: "var(--blue-fg)" }}
                  >
                    <div className="stack-h" style={{ gap: 8, alignItems: "flex-start" }}>
                      <IconAlerta size={15} />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                          {p.conhecido.titulo}
                          <span className="pill pill-info" style={{ marginLeft: 8, textTransform: "none" }}>
                            {p.conhecido.fonte === "manual"
                              ? t("manual do fabricante")
                              : p.conhecido.fonte === "campo+manual"
                              ? t("campo + manual")
                              : t("caso de campo")}
                          </span>
                        </div>
                        <div className="small" style={{ marginTop: 4 }}>{p.conhecido.causa}</div>
                        <div className="small" style={{ marginTop: 6, fontWeight: 500 }}>
                          {p.conhecido.acao}
                        </div>
                        {nav && p.conhecido.onde && (
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            style={{ marginTop: 8 }}
                            onClick={() => nav(p.conhecido.onde, { hostId: p.host_id, servico: p.servico })}
                          >
                            {t("ir para a tela que resolve")}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── Base de conhecimento ───────────────────────────────────── */}
      <div className="card">
        <div className="stack-h" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title" style={{ marginBottom: 4 }}>
              {t("O que o painel sabe reconhecer")}
            </div>
            <div className="small muted">
              {t("Casos de campo deste ambiente e o que o manual da NtechLab documenta. Cada erro reconhecido vira causa, ação e atalho.")}
            </div>
          </div>
          <button className="btn btn-secondary btn-sm" onClick={() => setVerCatalogo((v) => !v)}>
            {verCatalogo ? t("esconder") : t("ver base")}
          </button>
        </div>

        {verCatalogo && (
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead>
                <tr>
                  <th>{t("Situação")}</th>
                  <th>{t("Causa")}</th>
                  <th>{t("O que fazer")}</th>
                  <th>{t("Origem")}</th>
                </tr>
              </thead>
              <tbody>
                {(catalogo || []).map((c) => (
                  <tr key={c.chave}>
                    <td><strong>{c.titulo}</strong></td>
                    <td className="small">{c.causa}</td>
                    <td className="small">{c.acao}</td>
                    <td className="small muted">
                      {c.fonte === "manual"
                        ? t("manual")
                        : c.fonte === "campo+manual"
                        ? t("campo + manual")
                        : t("campo")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
