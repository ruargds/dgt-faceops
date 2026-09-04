import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes, formatData, formatDuracao } from "../../api";
import { t } from "../../i18n";
import {
  BarraMetrica, corDaSerie, Faisca, GraficoLinha, GraficoMultiLinha, tocarAlerta,
} from "../Graficos";
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
  disco_io:
    "Quanto do tempo o disco passou ocupado atendendo leitura e escrita — não é " +
    "espaço livre, é fila. Dá para estourar o teto de IOPS contratado com o " +
    "disco quase vazio: a latência dispara e tudo que toca disco trava junto.",
  gpu: "Quanto a placa de vídeo está trabalhando. É ela que faz o reconhecimento.",
  gpu_mem: "Memória da placa. Perto do limite, a próxima câmera causa falha.",
};

export default function MonitorView({ alvo, nav }) {
  const [resumo, setResumo] = useState(null);
  const [erro, setErro] = useState("");
  const [atualizadoEm, setAtualizadoEm] = useState(null);
  const [buscando, setBuscando] = useState(false);
  const [avisoColeta, setAvisoColeta] = useState("");
  // Milissegundos entre buscas. Começa conservador e passa a seguir o
  // que o servidor recomenda.
  const intervaloPoll = useRef(15000);
  const [carregando, setCarregando] = useState(true);
  const [detalhe, setDetalhe] = useState(alvo && alvo.hostId ? alvo.hostId : null);
  const [serie, setSerie] = useState(null);
  const [janela, setJanela] = useState(6);
  const [som, setSom] = useState(true);
  const [pico, setPico] = useState(null);
  const [serieDiscos, setSerieDiscos] = useState(null);
  const [verRecentes, setVerRecentes] = useState(false);
  const [recentes, setRecentes] = useState(null);
  const [recorrentes, setRecorrentes] = useState(null);
  // Comparação entre servidores — array (não Set) para dar referência
  // estável a dependência de efeito, sem expressão calculada no array de
  // deps.
  const [selecionados, setSelecionados] = useState([]);
  const [janelaComp, setJanelaComp] = useState(6);
  const [serieComp, setSerieComp] = useState(null);
  const [erroComp, setErroComp] = useState("");
  const pedidoComp = useRef(0);

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
    setBuscando(true);
    try {
      const r = await api.monitorResumo();
      setResumo(r);
      setAtualizadoEm(new Date());
      const sugerido = (r.coletor && r.coletor.poll_s) || 15;
      intervaloPoll.current = Math.max(10, sugerido) * 1000;
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
      setBuscando(false);
    }
  }, [som]);

  /**
   * O que o botão "Atualizar" faz.
   *
   * Manda COLETAR e só então relê. Reler sozinho não adiantaria: o
   * resumo é cacheado por ciclo do coletor, então sem coleta nova o
   * payload volta idêntico — o botão parecia funcionar e não fazia nada.
   */
  const atualizarAgora = useCallback(async () => {
    setBuscando(true);
    setAvisoColeta("");
    try {
      const r = await api.monitorColetar();
      if (r && r.ok === false) {
        // Cerca do servidor (coleta em andamento, ou cedo demais). Dizer
        // o motivo é melhor que um botão que não responde.
        setAvisoColeta(r.motivo || "não foi possível coletar agora");
      }
    } catch (ex) {
      setAvisoColeta(ex.message);
    } finally {
      // Relê de qualquer jeito: mesmo quando a coleta foi recusada, o
      // ciclo automático pode ter trazido algo novo.
      await carregar();
    }
  }, [carregar]);

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

  /**
   * Uma série por servidor selecionado, na mesma janela — é o que permite
   * ver se o pico de um coincide com o de outro. Continua lendo o banco do
   * painel (o mesmo endpoint do histórico de um host só, um por vez, em
   * paralelo); nenhum servidor é consultado de novo por causa disto.
   */
  const carregarComparacao = useCallback(async (hostIds, horas) => {
    if (!hostIds || hostIds.length < 2) return;
    const meu = ++pedidoComp.current;
    try {
      const resultados = await Promise.all(
        hostIds.map((id) =>
          api
            .monitorSerie(id, horas)
            .then((r) => ({ hostId: id, r }))
            .catch(() => ({ hostId: id, r: null }))
        )
      );
      if (meu === pedidoComp.current) {
        setSerieComp(resultados);
        setErroComp("");
      }
    } catch (ex) {
      if (meu === pedidoComp.current) setErroComp(ex.message);
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
        if (selecionados.length >= 2) await carregarComparacao(selecionados, janelaComp);
      }
      // Quem decide de quanto em quanto tempo perguntar é o SERVIDOR:
      // ele sabe se o coletor está em modo econômico. Buscar a cada 10 s
      // um dado que só muda a cada 5 min é pedir trabalho para nada.
      if (vivo) timer = setTimeout(tick, intervaloPoll.current);
    };
    timer = setTimeout(tick, intervaloPoll.current);

    const aoVoltar = () => {
      if (document.visibilityState === "visible" && vivo) carregar();
    };
    document.addEventListener("visibilitychange", aoVoltar);

    return () => {
      vivo = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", aoVoltar);
    };
  }, [carregar, carregarSerie, detalhe, janela, selecionados, janelaComp, carregarComparacao]);

  useEffect(() => {
    if (detalhe) {
      setSerie(null);
      carregarSerie(detalhe, janela);
      setPico(null);
      api.monitorPico(detalhe, 14).then(setPico).catch(() => setPico(null));
      setSerieDiscos(null);
      api.crescimentoDiscos(detalhe, janela).then(setSerieDiscos).catch(() => setSerieDiscos(null));
    }
  }, [detalhe, janela, carregarSerie]);

  useEffect(() => {
    if (selecionados.length >= 2) {
      setSerieComp(null);
      carregarComparacao(selecionados, janelaComp);
    } else {
      setSerieComp(null);
    }
  }, [selecionados, janelaComp, carregarComparacao]);

  function alternarSelecao(hostId) {
    setSelecionados((atual) =>
      atual.includes(hostId) ? atual.filter((id) => id !== hostId) : [...atual, hostId]
    );
  }
  function limparSelecao() {
    setSelecionados([]);
  }

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

  function selecionarTodos() {
    setSelecionados(servidores.filter((s) => s.ativo && s.monitorado).map((s) => s.host_id));
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
          {/* A hora da última leitura fica ao lado do botão. Sem ela,
              clicar em Atualizar quando nada mudou é indistinguível de
              um botão quebrado — e foi assim que este pareceu não
              funcionar. Agora o clique sempre deixa prova. */}
          {/* Largura reservada e texto FIXO. Antes, "Atualizar" virava
              "Atualizando…" e o carimbo de hora aparecia e sumia — a cada
              busca a linha inteira mudava de tamanho e a barra tremia de
              um lado para o outro. O que muda agora é só o ícone girar. */}
          <span
            className="small muted"
            style={{ minWidth: 132, textAlign: "right" }}
            title={t("Quando esta tela leu o painel pela última vez")}
          >
            {atualizadoEm
              ? `${t("atualizado às")} ${atualizadoEm.toLocaleTimeString([], {
                  hour: "2-digit", minute: "2-digit", second: "2-digit",
                })}`
              : ""}
          </span>
          <button
            className="btn btn-secondary"
            onClick={atualizarAgora}
            disabled={buscando}
            style={{ minWidth: 116, justifyContent: "center" }}
            title={t("Vai aos servidores agora, em vez de reler o que já está no banco")}
          >
            <IconAtualizar size={15} className={buscando ? "girando" : undefined} />{" "}
            {t("Atualizar")}
          </button>
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

      {avisoColeta && (
        <div
          className="card card-tight"
          style={{ background: "var(--amber-bg)", borderColor: "var(--amber-bd)", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "var(--amber-fg)" }}>
            {avisoColeta}
          </span>
        </div>
      )}

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
        <div className="stack-h" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            {t("Servidores")} · <span style={{ textTransform: "none", fontWeight: 400 }}>
              {t("clique num cartão para abrir o histórico")}
            </span>
          </div>
          {servidores.length > 1 && (
            <div className="stack-h" style={{ gap: 6 }}>
              <button type="button" className="btn btn-secondary btn-sm" onClick={selecionarTodos}>
                {t("Selecionar todos para comparar")}
              </button>
              {selecionados.length > 0 && (
                <button type="button" className="btn btn-secondary btn-sm" onClick={limparSelecao}>
                  {t("Limpar seleção")} ({selecionados.length})
                </button>
              )}
            </div>
          )}
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
              emComparacao={selecionados.includes(s.host_id)}
              onAlternarComparacao={() => alternarSelecao(s.host_id)}
            />
          ))}
        </div>
        </>
      )}

      {/* ── Comparação entre servidores ───────────────────────────────
          A pergunta que os cartões sozinhos não respondem: o pico de um
          servidor coincidiu com o de outro? Mesma janela, uma linha por
          servidor selecionado — cruzar visualmente é o diagnóstico. */}
      {selecionados.length >= 2 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="stack-h" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 8, marginBottom: 4 }}>
            <div className="section-title" style={{ marginBottom: 0 }}>
              {t("Comparação entre servidores")} · {selecionados.length} {t("selecionados")}
            </div>
            <div className="stack-h" style={{ gap: 6 }}>
              {JANELAS.map((j) => (
                <button
                  key={j.horas}
                  className={`btn btn-sm ${janelaComp === j.horas ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setJanelaComp(j.horas)}
                >
                  {j.rotulo}
                </button>
              ))}
            </div>
          </div>
          <div className="small muted" style={{ marginBottom: 14 }}>
            {t("Mesma janela para todos — se um pico aparece nas mesmas horas em mais de um servidor, um está empurrando o outro.")}
          </div>
          <Erro mensagem={erroComp} onTentar={() => carregarComparacao(selecionados, janelaComp)} />
          {!serieComp ? (
            <Carregando />
          ) : (
            <div className="stack-v" style={{ gap: 20 }}>
              <ComparacaoMetrica titulo={t("Processador em uso")} serieComp={serieComp} servidores={servidores} campo="cpu_uso" />
              <ComparacaoMetrica titulo={t("Memória")} serieComp={serieComp} servidores={servidores} campo="mem" />
              <ComparacaoMetrica titulo={t("Disco")} serieComp={serieComp} servidores={servidores} campo="disco" />
              <ComparacaoMetrica titulo={t("E/S do disco")} serieComp={serieComp} servidores={servidores} campo="disco_util_pct" />
            </div>
          )}
        </div>
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
              <Painel
                titulo={t("E/S do disco")}
                explicacao={EXPLICACAO.disco_io}
                serie={serie.amostras.map((a) => ({ ts: a.ts, valor: a.disco_util_pct }))}
                limite={85}
                legenda={`${serie.amostras.at(-1).disco_iops} operações/s`}
              />
              {/* Só quando há mais de um dispositivo: com um só, é a mesma
                  curva do painel acima — regra 4, não duplicar o que já
                  existe na tela. */}
              {serieDiscos && serieDiscos.series && serieDiscos.series.length > 1 && (
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13.5, marginBottom: 2 }}>
                    {t("Disco por dispositivo")}
                  </div>
                  <div className="small muted" style={{ marginBottom: 6 }}>
                    {t("Este servidor tem mais de um disco — utilização de E/S de cada um, na mesma janela.")}
                  </div>
                  <GraficoMultiLinha
                    series={serieDiscos.series.map((s, i) => ({
                      nome: s.dispositivo,
                      cor: corDaSerie(i),
                      pontos: s.pontos.map((p) => ({ ts: p.ts, valor: p.util_pct })),
                    }))}
                    altura={160}
                    escala="linear"
                    unidade="%"
                    formatar={(v) => `${v.toFixed(1)}%`}
                  />
                  <div className="stack-v" style={{ gap: 4, marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
                    {serieDiscos.series.map((s, i) => (
                      <div key={s.dispositivo} className="stack-h small" style={{ gap: 8, alignItems: "center" }}>
                        <span style={{ width: 10, height: 10, borderRadius: 2, background: corDaSerie(i), flexShrink: 0 }} />
                        <span className="mono" style={{ width: 100, flexShrink: 0 }}>{s.dispositivo}</span>
                        <span className="muted mono">
                          {t("média")} {s.util_media}% · {t("máx")} {s.util_pico}% ·{" "}
                          {t("agora")} {s.util_agora}% · {s.iops_agora} {t("operações/s")}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
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
        {/* O modo aparece porque muda o que a pessoa vê: em econômico, o
            ponto mais recente pode ter alguns minutos. Esconder isso faria
            o gráfico parecer atrasado sem explicação. */}
        O coletor lê cada servidor a cada {coletor.intervalo_s || 60} segundos
        {coletor.modo === "economico" ? (
          <>
            {" "}— <strong>{t("modo econômico")}</strong>, porque ninguém estava
            usando o painel. Ele acelera para {coletor.intervalo_ativo_s || 60}s
            assim que alguém abre, e a leitura mais recente já foi pedida.
          </>
        ) : (
          <>.</>
        )}{" "}
        {coletor.ciclos || 0} ciclo(s) desde que o painel subiu. Esta tela lê o
        histórico já gravado; não é ela que conversa com os servidores. Ajuste
        os limites e a cadência em <strong>{t("Configurações")}</strong>.
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

/**
 * Uma métrica, uma linha por servidor selecionado.
 *
 * Escala LINEAR fixa: os quatro campos aqui (`cpu_uso`, `mem`, `disco`,
 * `disco_util_pct`) são todos percentual 0–100, e a escala automática do
 * `GraficoMultiLinha` (log acima de 50× de razão) foi pensada para MB de
 * container, não para isto — um servidor ocioso a 1% ao lado de outro a
 * 60% bastaria para acionar o modo log e distorcer a leitura.
 */
/** Média, máximo e último valor da janela — o resumo que a legenda mostra. */
function estatisticasDaSerie(pontos) {
  if (!pontos || !pontos.length) return null;
  const valores = pontos.map((p) => p.valor);
  const soma = valores.reduce((acc, v) => acc + v, 0);
  return {
    media: soma / valores.length,
    maximo: Math.max(...valores),
    ultimo: valores[valores.length - 1],
  };
}

function ComparacaoMetrica({ titulo, serieComp, servidores, campo }) {
  const series = (serieComp || [])
    .filter((x) => x.r && x.r.amostras && x.r.amostras.length)
    .map((x, i) => {
      const host = servidores.find((sv) => sv.host_id === x.hostId);
      return {
        nome: (host && (host.rotulo || host.host)) || `host ${x.hostId}`,
        cor: corDaSerie(i),
        pontos: x.r.amostras
          .filter((a) => a[campo] !== null && a[campo] !== undefined)
          .map((a) => ({ ts: a.ts, valor: a[campo] })),
      };
    });

  return (
    <div>
      <div style={{ fontWeight: 600, fontSize: 13.5, marginBottom: 6 }}>{titulo}</div>
      <GraficoMultiLinha
        series={series}
        altura={190}
        escala="linear"
        unidade="%"
        formatar={(v) => `${v.toFixed(1)}%`}
      />
      {/* Legenda fixa com média/máx/agora — sem ela, o resumo só existia
          passando o mouse no gráfico, e é o número que decide "quem é o
          candidato" numa comparação de vários servidores de uma vez.
          Nome com largura fixa (não `flex: 1`): esticar até a borda
          jogava o resumo lá longe, sem relação visual com o nome. */}
      <div className="stack-v" style={{ gap: 4, marginTop: 10, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
        {series.map((s) => {
          const est = estatisticasDaSerie(s.pontos);
          return (
            <div key={s.nome} className="stack-h small" style={{ gap: 8, alignItems: "center" }}>
              <span
                style={{ width: 10, height: 10, borderRadius: 2, background: s.cor, flexShrink: 0 }}
              />
              <span
                className="mono"
                style={{ width: 190, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                title={s.nome}
              >
                {s.nome}
              </span>
              {est ? (
                <span className="muted mono">
                  {t("média")} {est.media.toFixed(1)}% · {t("máx")} {est.maximo.toFixed(1)}% ·{" "}
                  {t("agora")} {est.ultimo.toFixed(1)}%
                </span>
              ) : (
                <span className="muted">{t("sem dado no período")}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CartaoMonitor({ s, id, alertas, selecionado, onSelecionar, emComparacao, onAlternarComparacao }) {
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
        boxShadow: selecionado
          ? "0 0 0 3px rgba(26,111,196,.10)"
          : emComparacao
          ? "0 0 0 2px rgba(23,163,152,.35)"
          : undefined,
      }}
    >
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 2 }}>
        <div className="stack-h">
          <span className={`dot ${dot}`} />
          <strong style={{ fontSize: 15, color: "var(--titulo)" }}>{s.rotulo || s.host}</strong>
        </div>
        <div className="stack-h" style={{ gap: 8 }}>
          {s.tem_gpu && (
            <span className="pill pill-info" title={s.gpu_nome || t("GPU")}>
              <IconGPU size={12} /> {s.gpu_nome || t("GPU")}
            </span>
          )}
          {/* Comparar não é o mesmo clique que abre o histórico — por isso
              a propagação para para aqui. */}
          <label
            className="check"
            style={{ margin: 0 }}
            title={t("Marcar para comparar com outros servidores")}
            onClick={(e) => e.stopPropagation()}
          >
            <input type="checkbox" checked={emComparacao} onChange={onAlternarComparacao} />
            <span className="small muted">{t("comparar")}</span>
          </label>
        </div>
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
