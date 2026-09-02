import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes, formatData, formatDuracao } from "../../api";
import { t } from "../../i18n";
import { BarraMetrica, Faisca, GraficoLinha, tocarAlerta } from "../Graficos";
import { Carregando, Erro, Estatistica, Vazio } from "../Comuns";
import { IconAlerta, IconAtualizar, IconDownload, IconGPU, IconOk } from "../Icons";

const JANELAS = [
  { horas: 1, rotulo: "1 h" },
  { horas: 6, rotulo: "6 h" },
  { horas: 24, rotulo: "24 h" },
  { horas: 168, rotulo: "7 dias" },
  { horas: 720, rotulo: "30 dias" },
];

/**
 * O que cada número significa, em português de plantão.
 *
 * A tela é usada por quem nunca abriu o FindFace. "Carga por núcleo:
 * 1,4" não diz nada sozinho; "há processo esperando CPU" diz.
 */
/** "12,6 GB de 16,0 GB" — o absoluto que o percentual sozinho esconde. */
function deTotal(usadoMb, totalMb) {
  if (!totalMb) return "";
  return `${formatBytes(usadoMb * 1024 * 1024)} de ${formatBytes(totalMb * 1024 * 1024)}`;
}

function deTotalGb(usadoGb, totalGb) {
  if (!totalGb) return "";
  return `${formatBytes(usadoGb * 1024 ** 3)} de ${formatBytes(totalGb * 1024 ** 3)}`;
}

const EXPLICACAO = {
  cpu:
    "Quanto da CPU está sendo gasta agora (0 a 100%). A carga por núcleo, " +
    "logo abaixo, é outra coisa: é a fila de quem quer CPU — pode estar alta " +
    "com uso baixo quando os processos estão esperando disco.",
  mem: "Memória em uso, já descontando cache. Perto de 100%, o sistema começa a matar containers.",
  disco: "O disco mais cheio. Cheio, o banco para de gravar e o reconhecimento para junto.",
  gpu: "Quanto a placa de vídeo está trabalhando. É ela que faz o reconhecimento.",
  gpu_mem: "Memória da placa. Perto do limite, a próxima câmera causa falha.",
};

export default function MonitorView({ alvo, nav }) {
  const [resumo, setResumo] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [detalhe, setDetalhe] = useState(alvo && alvo.hostId ? alvo.hostId : null);
  const [serie, setSerie] = useState(null);
  const [janela, setJanela] = useState(6);
  const [som, setSom] = useState(true);
  const [pico, setPico] = useState(null);
  const [verRecentes, setVerRecentes] = useState(false);
  const [recentes, setRecentes] = useState(null);
  const [recorrentes, setRecorrentes] = useState(null);

  // Chegou aqui a partir de um alerta em outra tela ("ir para Monitor"):
  // abre direto no host certo, sem a pessoa precisar procurar de novo o
  // que o alerta já sabia. Só reage quando o alvo muda de verdade — não
  // pode disparar a cada nova referência de objeto do React.
  useEffect(() => {
    if (alvo && alvo.hostId) {
      setDetalhe(alvo.hostId);
      const temporizador = setTimeout(() => {
        const el = document.getElementById(`host-card-${alvo.hostId}`);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 80);
      return () => clearTimeout(temporizador);
    }
    return undefined;
  }, [alvo]);

  // Chaves dos alertas já anunciados. Sem isso o som tocaria a cada
  // atualização enquanto o problema durasse — e alerta que repete sem
  // parar é alerta que se aprende a ignorar.
  const anunciados = useRef(new Set());
  const primeiraCarga = useRef(true);
  // Sequência do último pedido de série. Descarta resposta fora de
  // ordem: sem isso, a série de '30 dias' (lenta) podia chegar depois
  // da de '1 h' e sobrescrever a tela com a janela errada — era isso
  // que fazia a troca de período parecer que não funcionava.
  const pedidoSerie = useRef(0);

  const carregar = useCallback(async () => {
    try {
      const r = await api.monitorResumo();
      setResumo(r);
      setErro("");

      const atuais = new Set((r.alertas || []).map((a) => `${a.host_id}:${a.chave}`));
      if (!primeiraCarga.current && som) {
        const novos = (r.alertas || []).filter(
          (a) => !anunciados.current.has(`${a.host_id}:${a.chave}`)
        );
        if (novos.length) {
          tocarAlerta(novos.some((a) => a.nivel === "critico") ? "critico" : "atencao");
        }
      }
      anunciados.current = atuais;
      primeiraCarga.current = false;
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, [som]);

  const carregarSerie = useCallback(async (hostId, horas) => {
    if (!hostId) return;
    const meu = ++pedidoSerie.current;
    try {
      const r = await api.monitorSerie(hostId, horas);
      if (meu === pedidoSerie.current) setSerie(r);
    } catch (ex) {
      if (meu === pedidoSerie.current) setErro(ex.message);
    }
  }, []);

  // Atualização de fundo.
  //
  // Barata de propósito: lê do banco do painel, nunca toca num servidor.
  // Quem fala com os servidores é o coletor, no ritmo dele. E para de
  // vez quando a aba perde o foco — nada rodando escondido sem ninguém
  // olhando.
  useEffect(() => {
    carregar();
    let vivo = true;
    let timer = null;

    const tick = async () => {
      if (!vivo) return;
      if (document.visibilityState === "visible") {
        await carregar();
        if (detalhe) await carregarSerie(detalhe, janela);
      }
      if (vivo) timer = setTimeout(tick, 10000);
    };
    timer = setTimeout(tick, 10000);

    const aoVoltar = () => {
      if (document.visibilityState === "visible" && vivo) carregar();
    };
    document.addEventListener("visibilitychange", aoVoltar);

    return () => {
      vivo = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", aoVoltar);
    };
  }, [carregar, carregarSerie, detalhe, janela]);

  useEffect(() => {
    if (detalhe) {
      setSerie(null);
      carregarSerie(detalhe, janela);
      setPico(null);
      api.monitorPico(detalhe, 14).then(setPico).catch(() => setPico(null));
    }
  }, [detalhe, janela, carregarSerie]);

  useEffect(() => {
    if (!verRecentes) return;
    api.incidentesRecentes(3).then((r) => setRecentes(r.incidentes)).catch(() => setRecentes([]));
  }, [verRecentes]);

  // Reincidência: leitura barata (contagem no banco do painel), uma vez
  // ao abrir. Não entra no laço de 10s — o que repete em 14 dias não muda
  // de dez em dez segundos.
  useEffect(() => {
    api.reincidencia(14).then((r) => setRecorrentes(r.itens)).catch(() => setRecorrentes([]));
  }, []);

  if (carregando && !resumo) return <Carregando texto={t("Lendo o histórico…")} />;

  const alertas = (resumo && resumo.alertas) || [];
  const servidores = (resumo && resumo.servidores) || [];
  const coletor = (resumo && resumo.coletor) || {};
  const criticos = alertas.filter((a) => a.nivel === "critico");
  const incidentesAbertos = (resumo && resumo.incidentes_abertos) || [];
  const painel = (resumo && resumo.painel) || {};

  const hostDetalhe = servidores.find((s) => s.host_id === detalhe);

  function irParaHost(hostId) {
    setDetalhe(hostId);
    const el = document.getElementById(`host-card-${hostId}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <>
      <div className="page-head" style={{ marginBottom: 14 }}>
        <div>
          <div className="page-title">{t("tela.monitor")}</div>
          <div className="page-sub">
            {t("tela.monitor.sub")}
          </div>
        </div>
        <div className="page-actions">
          <label className="check" style={{ margin: 0 }}>
            <input type="checkbox" checked={som} onChange={(e) => setSom(e.target.checked)} />
            <span>{t("Aviso sonoro")}</span>
          </label>
          <button className="btn btn-secondary" onClick={carregar}>
            <IconAtualizar size={15} /> {t("Atualizar")}</button>
        </div>
      </div>

      {/* ── Resumo do topo ───────────────────────────────────────────
          Números absolutos, não só percentual. Tudo daqui vem do mesmo
          `/api/monitor/resumo` (banco do painel + disco local) — nenhum
          SSH, para a tela inicial não bater nas VMs de produção a cada
          abertura. */}
      <div className="grid-stats" style={{ marginBottom: 16 }}>
        <Estatistica
          rotulo={t("Servidores monitorados")}
          valor={`${servidores.filter((s) => s.ativo && s.monitorado).length} de ${servidores.length}`}
          sub={t("ativos e sob coleta contínua")}
        />
        <Estatistica
          rotulo={t("Serviços fora do ar")}
          valor={incidentesAbertos.filter((i) => i.tipo === "servico").length}
          sub={
            incidentesAbertos.length
              ? t("veja o detalhe em Serviços por máquina")
              : t("nenhum serviço em queda agora")
          }
        />
        {/* Backup e disco do painel só vêm do servidor para quem tem
            permissão de backup — sem permissão, o campo nem existe na
            resposta e o cartão não aparece. */}
        {painel.backups_com_falha !== undefined && (
          <Estatistica
            rotulo={t("Backups com falha")}
            valor={painel.backups_com_falha}
            sub={
              painel.servidores_sem_backup
                ? `${painel.servidores_sem_backup} ${t("servidor(es) sem backup nenhum")}`
                : t("último backup de cada servidor")
            }
          />
        )}
        {painel.armazenamento && (
          <Estatistica
            rotulo={t("Disco de backup do painel")}
            valor={`${formatBytes(painel.armazenamento.usado_bytes)} de ${formatBytes(painel.armazenamento.total_bytes)}`}
            sub={`${formatBytes(painel.armazenamento.livre_bytes)} ${t("livres")}`}
            pct={painel.armazenamento.percentual}
          />
        )}
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {/* ── Alertas ──────────────────────────────────────────────── */}
      {alertas.length === 0 ? (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 16 }}
        >
          <div className="stack-h small" style={{ color: "var(--green-fg)" }}>
            <IconOk size={16} />
            <strong>{t("Tudo em ordem.")}</strong>
            <span>{t("Nenhum servidor com problema no momento.")}</span>
          </div>
        </div>
      ) : (
        <div style={{ marginBottom: 16 }}>
          <div className="section-title" style={{ marginBottom: 8 }}>
            {criticos.length > 0
              ? `${criticos.length} problema(s) grave(s) e ${alertas.length - criticos.length} aviso(s)`
              : `${alertas.length} aviso(s)`}
          </div>
          <div className="stack-v" style={{ gap: 8 }}>
            {alertas.map((a, i) => {
              const grave = a.nivel === "critico";
              return (
                <div
                  key={`${a.host_id}-${a.chave}-${i}`}
                  className="card card-tight"
                  onClick={() => irParaHost(a.host_id)}
                  title={t("Ver este servidor abaixo")}
                  style={{
                    background: grave ? "var(--red-bg)" : "var(--amber-bg)",
                    borderColor: grave ? "var(--red-bd)" : "var(--amber-bd)",
                    borderLeftWidth: 4,
                    borderLeftColor: grave ? "var(--red)" : "var(--amber)",
                    cursor: "pointer",
                  }}
                >
                  <div
                    className="stack-h"
                    style={{ alignItems: "flex-start", gap: 10, color: grave ? "var(--red-fg)" : "var(--amber-fg)" }}
                  >
                    <IconAlerta size={17} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                        {a.rotulo || a.host} — {a.texto}
                        {a.desde && (
                          <span className="small" style={{ fontWeight: 400, opacity: 0.85 }}>
                            {" "}· {t("há")} {formatDuracao(a.duracao_s)}
                          </span>
                        )}
                      </div>
                      {/* O que o número quer dizer, antes do que fazer:
                          é a pergunta que vem primeiro para quem não
                          opera o FindFace todo dia. */}
                      {a.significa && (
                        <div className="small" style={{ marginTop: 4, opacity: 0.92 }}>
                          {a.significa}
                        </div>
                      )}
                      {a.acao && (
                        <div className="small" style={{ marginTop: 4, opacity: 0.78 }}>
                          {a.acao}
                        </div>
                      )}
                    </div>
                    {/* Atalho de verdade: leva à tela e já com o
                        host/serviço certos — não é mais só um rótulo. */}
                    {a.onde && (
                      <button
                        type="button"
                        className="pill"
                        style={{ background: "rgba(0,0,0,.08)", border: "none", cursor: nav && a.onde_aba ? "pointer" : "default" }}
                        onClick={(e) => {
                          e.stopPropagation();
                          if (nav && a.onde_aba) {
                            nav(a.onde_aba, { hostId: a.host_id, servico: a.servico || a.chave });
                          } else {
                            irParaHost(a.host_id);
                          }
                        }}
                      >
                        {t("ir para")} {a.onde}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Cartões ──────────────────────────────────────────────── */}
      {servidores.length === 0 ? (
        <Vazio titulo={t("Nenhum servidor cadastrado")}>{t("Cadastre as máquinas em")} <strong>{t("Servidores")}</strong> {t("para o monitor começar a acompanhar.")}</Vazio>
      ) : (
        <>
        <div className="section-title" style={{ marginBottom: 8 }}>
          {t("Servidores")} · <span style={{ textTransform: "none", fontWeight: 400 }}>
            {t("clique num cartão para abrir o histórico")}
          </span>
        </div>
        <div className="grid-cards">
          {servidores.map((s) => (
            <CartaoMonitor
              key={s.host_id}
              id={`host-card-${s.host_id}`}
              s={s}
              alertas={alertas.filter((a) => a.host_id === s.host_id)}
              selecionado={detalhe === s.host_id}
              onSelecionar={() => setDetalhe(detalhe === s.host_id ? null : s.host_id)}
            />
          ))}
        </div>
        </>
      )}

      {/* ── Reincidência ─────────────────────────────────────────────
          Faixa curta, só quando há o que dizer: "isto não é a primeira
          vez" é a informação que muda a conduta de quem está de plantão. */}
      {recorrentes && recorrentes.length > 0 && (
        <div
          className="card card-tight"
          style={{
            marginTop: 16,
            background: "var(--amber-bg)",
            borderColor: "var(--amber-bd)",
            borderLeftWidth: 4,
            borderLeftColor: "var(--amber)",
          }}
        >
          <div className="stack-h" style={{ gap: 10, alignItems: "flex-start", color: "var(--amber-fg)" }}>
            <IconAlerta size={17} />
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                {recorrentes.length === 1
                  ? t("1 problema que já repetiu nos últimos 14 dias")
                  : `${recorrentes.length} ${t("problemas que já repetiram nos últimos 14 dias")}`}
              </div>
              <div className="small" style={{ marginTop: 4, opacity: 0.92 }}>
                {recorrentes.slice(0, 3).map((r) => (
                  <div key={`${r.host_id}-${r.servico}`}>
                    <span className="mono">{r.servico || t("máquina inteira")}</span>
                    {" — "}{r.ocorrencias}× {t("em")} {r.host}
                    {r.hora_tipica !== null && `, ${t("quase sempre por volta das")} ${String(r.hora_tipica).padStart(2, "0")}h`}
                  </div>
                ))}
              </div>
            </div>
            {nav && (
              <button
                type="button"
                className="pill"
                style={{ background: "rgba(0,0,0,.08)", border: "none", cursor: "pointer" }}
                onClick={() => nav("diagnostico")}
              >
                {t("ver Diagnóstico")}
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Serviços por máquina ─────────────────────────────────────
          O que o painel de alertas não mostrava: qual serviço, em qual
          host, desde quando — e a causa provável, quando dá para
          adivinhar pelo que o Docker já contou (OOM, código de saída,
          healthcheck, reinícios em loop). */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 10 }}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            {t("Serviços por máquina")}
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setVerRecentes((v) => !v)}
          >
            {verRecentes ? t("Só os em aberto") : t("Ver últimos 3 dias")}
          </button>
        </div>

        {(() => {
          const lista = verRecentes ? (recentes || []) : incidentesAbertos;
          if (verRecentes && recentes === null) return <Carregando />;
          if (lista.length === 0) {
            return (
              <div className="small muted">
                {verRecentes
                  ? t("Nenhum serviço caiu nos últimos 3 dias.")
                  : t("Nenhum serviço com problema agora.")}
              </div>
            );
          }
          const porHost = {};
          for (const inc of lista) {
            (porHost[inc.host_id] = porHost[inc.host_id] || []).push(inc);
          }
          return (
            <div className="stack-v" style={{ gap: 14 }}>
              {Object.entries(porHost).map(([hostId, incs]) => {
                const s = servidores.find((x) => String(x.host_id) === String(hostId));
                return (
                  <div key={hostId}>
                    <div
                      className="stack-h small"
                      style={{ gap: 6, marginBottom: 6, cursor: "pointer" }}
                      onClick={() => irParaHost(Number(hostId))}
                    >
                      <span className={`dot ${incs.some((i) => i.aberto && i.nivel === "critico") ? "dot-err" : incs.some((i) => i.aberto) ? "dot-warn" : "dot-ok"}`} />
                      <strong>{s ? (s.rotulo || s.host) : `host ${hostId}`}</strong>
                    </div>
                    <div className="table-wrap">
                      <table>
                        <thead>
                          <tr>
                            <th>{t("Serviço")}</th>
                            <th>{t("Situação")}</th>
                            <th>{t("Causa provável")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {incs.map((inc) => (
                            <tr key={inc.id}>
                              <td className="mono">{inc.servico || "—"}</td>
                              <td>
                                {inc.aberto ? (
                                  <span className={`pill ${inc.nivel === "critico" ? "pill-err" : "pill-warn"}`}>
                                    {t("parado há")} {formatDuracao(inc.duracao_s)}
                                  </span>
                                ) : (
                                  <span className="pill pill-ok">
                                    {t("voltou")} {formatData(inc.fim)} · {t("ficou fora")} {formatDuracao(inc.duracao_s)}
                                  </span>
                                )}
                              </td>
                              <td className="small muted">{inc.causa_provavel || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })()}
      </div>

      {/* ── Detalhe ──────────────────────────────────────────────── */}
      {detalhe && hostDetalhe && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 14 }}>
            <div>
              <div className="section-title" style={{ marginBottom: 2 }}>
                Histórico de {hostDetalhe.rotulo || hostDetalhe.host}
              </div>
              <div className="small muted">
                {serie ? `${serie.total} amostra(s) no período` : "carregando…"}
              </div>
            </div>
            <div className="stack-h" style={{ gap: 6 }}>
              {JANELAS.map((j) => (
                <button
                  key={j.horas}
                  className={`btn btn-sm ${janela === j.horas ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setJanela(j.horas)}
                >
                  {j.rotulo}
                </button>
              ))}
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() =>
                  api
                    .baixar(api.urlExportarMonitor(detalhe, janela), `monitor-${hostDetalhe.host}-${janela}h.csv`)
                    .catch((e) => setErro(e.message))
                }
                title={t("Baixar o histórico bruto em CSV")}
              >
                <IconDownload size={13} />
              </button>
            </div>
          </div>

          {/* Horário de pico — média por hora do dia nos últimos 14 dias.
              Agregação sobre o histórico já gravado, sem modelo nenhum:
              responde "quando esta máquina mais trabalha" sem custar mais
              que a consulta que a tela já fazia. */}
          {pico && pico.horas.some((h) => h.amostras > 0) && (
            <div style={{ marginBottom: 16 }}>
              <div className="small muted" style={{ marginBottom: 6 }}>
                {t("Horário de pico (média de CPU por hora, últimos 14 dias)")}
              </div>
              <div className="stack-h" style={{ gap: 2, alignItems: "flex-end", height: 40 }}>
                {pico.horas.map((h, hora) => {
                  const v = h.cpu || 0;
                  const cor = v >= 90 ? "var(--red)" : v >= 70 ? "var(--amber)" : "var(--blue)";
                  return (
                    <div
                      key={hora}
                      title={`${String(hora).padStart(2, "0")}h — cpu ${h.cpu ?? "—"}% · mem ${h.mem ?? "—"}%`}
                      style={{
                        flex: 1, height: `${Math.max(3, Math.min(100, v))}%`,
                        background: h.amostras ? cor : "var(--border)",
                        borderRadius: 2,
                      }}
                    />
                  );
                })}
              </div>
              <div className="stack-h small muted" style={{ justifyContent: "space-between", marginTop: 2 }}>
                <span>00h</span><span>06h</span><span>12h</span><span>18h</span><span>23h</span>
              </div>
            </div>
          )}

          {!serie ? (
            <Carregando />
          ) : serie.amostras.length === 0 ? (
            <div className="small muted">
              Sem amostras nesse período. O coletor grava a cada{" "}
              {coletor.intervalo_s || 60} segundos — se o servidor foi cadastrado
              agora, aguarde um minuto.
            </div>
          ) : (
            <div className="stack-v" style={{ gap: 18 }}>
              {/* Uso real quando existe medição; amostra antiga não tem, e
                  aí o ponto some do gráfico em vez de virar 0%. */}
              <Painel
                titulo={t("Processador em uso")}
                explicacao={EXPLICACAO.cpu}
                serie={serie.amostras
                  .filter((a) => a.cpu_uso !== null && a.cpu_uso !== undefined)
                  .map((a) => ({ ts: a.ts, valor: a.cpu_uso }))}
                limite={90}
                legenda={
                  serie.amostras.at(-1).cpu_uso === null ||
                  serie.amostras.at(-1).cpu_uso === undefined
                    ? "sem medição de uso nesta amostra"
                    : `agora: ${serie.amostras.at(-1).cpu_uso}%`
                }
              />
              <Painel
                titulo="Carga (fila por núcleo)"
                explicacao={
                  "Quantos processos querem CPU ao mesmo tempo, por núcleo. " +
                  "Acima de 1,00 há alguém esperando a vez."
                }
                serie={serie.amostras.map((a) => ({ ts: a.ts, valor: a.cpu }))}
                limite={90}
                maximo={200}
                legenda={`agora: ${serie.amostras.at(-1).carga} por núcleo`}
              />
              <Painel
                titulo={t("Memória")}
                explicacao={EXPLICACAO.mem}
                serie={serie.amostras.map((a) => ({ ts: a.ts, valor: a.mem }))}
                limite={90}
                legenda={
                  deTotal(serie.amostras.at(-1).mem_usado_mb, serie.amostras.at(-1).mem_total_mb)
                    ? `${deTotal(serie.amostras.at(-1).mem_usado_mb, serie.amostras.at(-1).mem_total_mb)} · ${serie.amostras.at(-1).mem}%`
                    : `agora: ${serie.amostras.at(-1).mem}%`
                }
              />
              <Painel
                titulo={t("Disco")}
                explicacao={EXPLICACAO.disco}
                serie={serie.amostras.map((a) => ({ ts: a.ts, valor: a.disco }))}
                limite={90}
                legenda={
                  `${serie.amostras.at(-1).disco_ponto} — ` +
                  (deTotalGb(
                    serie.amostras.at(-1).disco_total_gb - serie.amostras.at(-1).disco_livre_gb,
                    serie.amostras.at(-1).disco_total_gb
                  ) || `${serie.amostras.at(-1).disco_livre_gb} GB livres`) +
                  ` · ${serie.amostras.at(-1).disco_livre_gb} GB livres`
                }
              />
              {serie.tem_gpu && (
                <>
                  <Painel
                    titulo={
                      hostDetalhe.gpu_nome
                        ? `${t("Placa de vídeo")} — ${t("uso")} · ${hostDetalhe.gpu_nome}`
                        : `${t("Placa de vídeo")} — ${t("uso")}`
                    }
                    explicacao={EXPLICACAO.gpu}
                    serie={serie.amostras.map((a) => ({ ts: a.ts, valor: a.gpu }))}
                    limite={95}
                    legenda={`${serie.amostras.at(-1).gpu}% · ${serie.amostras.at(-1).gpu_temp} °C`}
                  />
                  <Painel
                    titulo={t("Placa de vídeo — memória")}
                    explicacao={EXPLICACAO.gpu_mem}
                    serie={serie.amostras.map((a) => ({ ts: a.ts, valor: a.gpu_mem }))}
                    limite={92}
                    legenda={
                      deTotal(
                        serie.amostras.at(-1).gpu_mem_usado_mb,
                        serie.amostras.at(-1).gpu_mem_total_mb
                      )
                        ? `${deTotal(serie.amostras.at(-1).gpu_mem_usado_mb, serie.amostras.at(-1).gpu_mem_total_mb)} · ${serie.amostras.at(-1).gpu_mem}%`
                        : `agora: ${serie.amostras.at(-1).gpu_mem}%`
                    }
                  />
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Rodapé ───────────────────────────────────────────────── */}
      <div className="small muted" style={{ marginTop: 16 }}>
        O coletor lê cada servidor a cada {coletor.intervalo_s || 60} segundos —{" "}
        {coletor.ciclos || 0} ciclo(s) desde que o painel subiu. Esta tela lê o
        histórico já gravado; não é ela que conversa com os servidores. Ajuste
        os limites de alerta em <strong>{t("Configurações")}</strong>.
      </div>
    </>
  );
}

function Painel({ titulo, explicacao, serie, limite, legenda, maximo = 100 }) {
  return (
    <div>
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 2 }}>
        <span style={{ fontWeight: 600, fontSize: 13.5 }}>{titulo}</span>
        <span className="small mono muted">{legenda}</span>
      </div>
      <div className="small muted" style={{ marginBottom: 6 }}>{explicacao}</div>
      <GraficoLinha
        serie={serie}
        limite={limite}
        maximo={maximo}
        altura={110}
        rotulo={titulo}
      />
    </div>
  );
}

function CartaoMonitor({ s, id, alertas, selecionado, onSelecionar }) {
  const a = s.amostra;
  const grave = alertas.some((x) => x.nivel === "critico");
  const aviso = alertas.length > 0;

  let dot = "dot-idle";
  let estado = "sem dados ainda";
  if (!s.ativo) estado = "desativado";
  else if (!s.monitorado) estado = "não monitorado";
  else if (a && a.erro) { dot = "dot-err"; estado = "sem comunicação"; }
  else if (grave) { dot = "dot-err"; estado = "problema grave"; }
  else if (aviso) { dot = "dot-warn"; estado = "atenção"; }
  else if (a) { dot = "dot-ok"; estado = "normal"; }

  return (
    <div
      id={id}
      className="card"
      onClick={onSelecionar}
      style={{
        cursor: "pointer",
        opacity: s.ativo ? 1 : 0.6,
        borderColor: selecionado ? "var(--blue)" : grave ? "var(--red-bd)" : "var(--border)",
        boxShadow: selecionado ? "0 0 0 3px rgba(26,111,196,.10)" : undefined,
      }}
    >
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 2 }}>
        <div className="stack-h">
          <span className={`dot ${dot}`} />
          <strong style={{ fontSize: 15, color: "var(--titulo)" }}>{s.rotulo || s.host}</strong>
        </div>
        {s.tem_gpu && (
          <span className="pill pill-info" title={s.gpu_nome || t("GPU")}>
            <IconGPU size={12} /> {s.gpu_nome || t("GPU")}
          </span>
        )}
      </div>

      <div className="small muted" style={{ marginBottom: 12 }}>
        <span className="mono">{s.endereco}</span> · {estado}
      </div>

      {!a ? (
        <div className="small muted">{t("Aguardando a primeira leitura do coletor.")}</div>
      ) : a.erro ? (
        <div className="small" style={{ color: "var(--red)" }}>{a.erro}</div>
      ) : (
        <>
          {a.cpu_uso !== null && a.cpu_uso !== undefined ? (
            <BarraMetrica
              rotulo={t("Processador")}
              valor={a.cpu_uso}
              limite={90}
              detalhe={`${a.carga} por núcleo de fila`}
            />
          ) : (
            <BarraMetrica
              rotulo={t("Carga por núcleo")}
              valor={Math.min(a.cpu, 100)}
              limite={90}
              detalhe={`${a.carga} por núcleo — uso de CPU ainda não medido`}
            />
          )}
          <BarraMetrica
            rotulo={t("Memória")}
            valor={a.mem}
            limite={90}
            detalhe={deTotal(a.mem_usado_mb, a.mem_total_mb)}
          />
          <BarraMetrica
            rotulo={t("Disco")}
            valor={a.disco}
            limite={90}
            detalhe={
              `${a.disco_ponto} — ` +
              (deTotalGb(a.disco_total_gb - a.disco_livre_gb, a.disco_total_gb) ||
                `${a.disco_livre_gb} GB`) +
              ` · ${a.disco_livre_gb} GB livres`
            }
          />
          {s.tem_gpu && (
            <BarraMetrica
              rotulo={t("Memória de vídeo")}
              valor={a.gpu_mem}
              limite={92}
              detalhe={
                [
                  deTotal(a.gpu_mem_usado_mb, a.gpu_mem_total_mb),
                  a.gpu_temp ? `${a.gpu_temp} °C` : "",
                ].filter(Boolean).join(" · ")
              }
            />
          )}

          <div
            className="stack-h small muted"
            style={{ justifyContent: "space-between", borderTop: "1px solid var(--border)", paddingTop: 8, marginTop: 4 }}
          >
            <span>
              {a.cont_total > 0
                ? `${a.cont_rodando} de ${a.cont_total} serviços`
                : "sem serviços do FindFace"}
            </span>
            <span title={t("Quanto a leitura custou")}>{a.coleta_ms} ms</span>
          </div>
          <div className="small muted" style={{ marginTop: 2 }}>
            Última leitura: {formatData(a.ts)}
          </div>
        </>
      )}
    </div>
  );
}
