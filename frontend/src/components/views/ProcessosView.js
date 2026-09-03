import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes, nivel } from "../../api";
import { t } from "../../i18n";
import {
  ajudaDeBusca, casaBusca, termosDaBusca,
} from "../../utils/buscaInteligente";
import { usePermissions } from "../../usePermissions";
import { Carregando, Erro, SeletorHost, Vazio, useHosts } from "../Comuns";
import { IconAtualizar } from "../Icons";

/**
 * Processos ao vivo — um "htop" explicado.
 *
 * A mesma informação do htop, mas traduzida para quem opera no N1:
 * o topo diz "quão ocupada está a máquina" e a tabela "quem está
 * consumindo". Atualiza sozinho a cada 2,5s e para quando a aba perde o
 * foco — nada sondando servidor escondido.
 */
const INTERVALO_MS = 2500;

export default function ProcessosView() {
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();
  const { has } = usePermissions();
  // Reinício reaproveita a MESMA rota cercada da tela de Serviços — nada
  // de matar PID: ver o rodapé da tabela.
  const podeReiniciar = has("services.restart");
  const [reiniciando, setReiniciando] = useState("");
  const [avisoAcao, setAvisoAcao] = useState("");
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  // Ordenação por coluna. Números (pid/cpu/mem) ordenam 0-9; texto
  // (usuário/tempo/programa) ordena A-Z. Clicar no cabeçalho inverte.
  const [buscaProc, setBuscaProc] = useState("");
  const [ordem, setOrdem] = useState({ campo: "cpu", dir: "desc" });
  const [pausado, setPausado] = useState(false);
  const pedido = useRef(0);

  const carregar = useCallback(async () => {
    if (!hostId) return;
    const meu = ++pedido.current;
    try {
      const r = await api.processos(hostId);
      if (meu === pedido.current) {
        setDados(r);
        setErro("");
      }
    } catch (ex) {
      if (meu === pedido.current) setErro(ex.message);
    }
  }, [hostId]);

  // Troca de servidor limpa a tela na hora
  useEffect(() => {
    setDados(null);
    setErro("");
  }, [hostId]);

  // Laço de atualização; respeita foco da aba e o botão pausar.
  useEffect(() => {
    if (!hostId) return undefined;
    let vivo = true;
    let timer = null;

    const tick = async () => {
      if (!vivo) return;
      if (!pausado && document.visibilityState === "visible") {
        await carregar();
      }
      if (vivo) timer = setTimeout(tick, INTERVALO_MS);
    };
    // primeira leitura imediata
    (async () => {
      if (document.visibilityState === "visible") await carregar();
      if (vivo) timer = setTimeout(tick, INTERVALO_MS);
    })();

    return () => {
      vivo = false;
      if (timer) clearTimeout(timer);
    };
  }, [hostId, pausado, carregar]);

  async function reiniciarContainer(container) {
    if (!window.confirm(`Reiniciar o container ${container}?`)) return;
    setReiniciando(container);
    setAvisoAcao("");
    try {
      const r = await api.reiniciarContainer(hostId, container);
      setAvisoAcao(`${container} reiniciado — estado atual: ${r.estado}.`);
    } catch (ex) {
      setAvisoAcao(`Falhou: ${ex.message}`);
    } finally {
      setReiniciando("");
    }
  }

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo={t("Cadastre um servidor primeiro")} />;

  const NUM = new Set(["pid", "cpu", "mem", "gpu_bytes"]);
  // A ordenação escolhida na tela manda: aqui a busca só REDUZ a lista,
  // sem reordenar por relevância. Trocar a ordem de quem clicou numa
  // coluna seria tirar da pessoa o controle que ela acabou de exercer.
  const processos = dados
    ? [...dados.processos]
        .filter((p) =>
          casaBusca(termosDaBusca(buscaProc), p.comando, p.usuario, p.container, String(p.pid)),
        )
        .sort((a, b) => {
        const { campo, dir } = ordem;
        let r;
        if (NUM.has(campo)) {
          r = (a[campo] || 0) - (b[campo] || 0);
        } else {
          r = String(a[campo] || "").localeCompare(String(b[campo] || ""), "pt-BR", {
            numeric: true,
            sensitivity: "base",
          });
        }
        return dir === "asc" ? r : -r;
      })
    : [];

  const ordenarPor = (campo) =>
    setOrdem((o) =>
      o.campo === campo
        ? { campo, dir: o.dir === "asc" ? "desc" : "asc" }
        : { campo, dir: NUM.has(campo) ? "desc" : "asc" }
    );

  return (
    <>
      <div className="page-head" style={{ marginBottom: 14 }}>
        <div>
          <div className="page-title">{t("tela.processos")}</div>
          <div className="page-sub">
            {t("tela.processos.sub")}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <button
            className={`btn btn-sm ${pausado ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setPausado((v) => !v)}
          >
            {pausado ? "Retomar" : "Pausar"}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={carregar} title={t("Atualizar agora")}>
            <IconAtualizar size={14} />
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {!dados && !erro && <Carregando texto={t("Lendo os processos do servidor…")} />}

      {dados && (
        <>
          {avisoAcao && (
            <div className="card card-tight" style={{ marginBottom: 12 }}>
              <span className="small">{avisoAcao}</span>
            </div>
          )}
          <Resumo d={dados} />

          <div className="card" style={{ marginTop: 16 }}>
            <div className="section-title" style={{ marginBottom: 8 }}>{t("Processos que mais consomem")} <span className="small muted" style={{ fontWeight: 400 }}>
                {" "}— clique numa coluna para ordenar
              </span>
            </div>

            <div className="small muted" style={{ marginBottom: 10 }}>
              <strong>{t("CPU")}</strong> é o quanto de um núcleo o processo usa agora
              (pode passar de 100% se usa vários). <strong>{t("Memória")}</strong> é a
              fatia da memória física. <strong>{t("Tempo")}</strong> é quanto de CPU ele
              já acumulou desde que iniciou.
              {dados.tem_gpu && (
                <> <strong>{t("GPU")}</strong> é a memória de vídeo que o processo
                  reservou — a mesma leitura que aparece em Recursos.</>
              )}
            </div>

            <div className="filtros" style={{ marginBottom: 10 }}>
              <div className="filtro-busca">
                <input
                  type="search"
                  value={buscaProc}
                  onChange={(e) => setBuscaProc(e.target.value)}
                  placeholder={t("Buscar processo…")}
                  title={ajudaDeBusca(t("Procura no comando, no usuário, no container e no PID."))}
                  aria-label={t("Buscar processo")}
                />
              </div>
              <span className="small muted">
                {buscaProc
                  ? `${processos.length} ${t("de")} ${dados.processos.length}`
                  : `${dados.processos.length} ${t("processo(s)")}`}
              </span>
            </div>

            <div className="table-wrap">
              <table className="tabela-densa">
                <thead>
                  <tr>
                    <Th campo="pid" ordem={ordem} onClick={ordenarPor} className="right">{t("PID")}</Th>
                    <Th campo="usuario" ordem={ordem} onClick={ordenarPor}>{t("Usuário")}</Th>
                    <Th campo="cpu" ordem={ordem} onClick={ordenarPor} className="right" style={{ width: 120 }}>{t("CPU")}</Th>
                    <Th campo="mem" ordem={ordem} onClick={ordenarPor} className="right" style={{ width: 120 }}>{t("Memória")}</Th>
                    {dados.tem_gpu && (
                      <Th campo="gpu_bytes" ordem={ordem} onClick={ordenarPor} className="right">{t("GPU")}</Th>
                    )}
                    <Th campo="tempo" ordem={ordem} onClick={ordenarPor} className="right">{t("Tempo")}</Th>
                    <Th campo="comando" ordem={ordem} onClick={ordenarPor}>{t("Programa")}</Th>
                    {/* Serviço dono do processo. É o que transforma
                        "python3 consumindo 31%" em algo acionável. */}
                    <Th campo="container" ordem={ordem} onClick={ordenarPor}>{t("Serviço")}</Th>
                    {podeReiniciar && <th style={{ width: 1 }} />}
                  </tr>
                </thead>
                <tbody>
                  {processos.map((p) => (
                    <tr key={p.pid}>
                      <td className="right mono small">{p.pid}</td>
                      <td className="small">{p.usuario}</td>
                      <CelulaPct valor={p.cpu} />
                      <CelulaPct valor={p.mem} />
                      {dados.tem_gpu && (
                        <td className="right mono small">
                          {p.gpu_bytes ? formatBytes(p.gpu_bytes) : <span className="muted">—</span>}
                        </td>
                      )}
                      <td className="right mono small">{p.tempo}</td>
                      <td className="mono small" style={{ maxWidth: 280, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={p.comando}>
                        {p.comando}
                      </td>
                      <td className="mono small" style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={p.container}>
                        {p.container || <span className="muted">—</span>}
                      </td>
                      {podeReiniciar && (
                        <td>
                          {p.container ? (
                            <button
                              type="button"
                              className="btn btn-secondary btn-sm"
                              disabled={reiniciando === p.container}
                              onClick={() => reiniciarContainer(p.container)}
                              title={t("Reinicia o container inteiro, pela mesma rota cercada da tela de Serviços")}
                            >
                              {reiniciando === p.container ? t("reiniciando…") : t("reiniciar")}
                            </button>
                          ) : null}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {podeReiniciar && (
              <div className="small muted" style={{ marginTop: 10 }}>
                O botão reinicia o <strong>container</strong> dono do processo, não o
                PID. É de propósito: matar processo solto num servidor de
                reconhecimento facial pode corromper banco, e a rota de reinício já
                tem a cerca que só age em container do projeto do FindFace e recusa
                agir durante limpeza de eventos.
              </div>
            )}
          </div>

          <div className="small muted" style={{ marginTop: 12 }}>
            Atualiza a cada {INTERVALO_MS / 1000}s enquanto esta aba está à frente
            {pausado ? " (pausado)" : ""}. Para o servidor não paga por isso: é
            uma leitura leve por SSH, sob demanda.
          </div>
        </>
      )}
    </>
  );
}

/**
 * Célula de percentual no formato gerenciador de tarefas: o número à
 * direita, uma barra fina embaixo dele, tudo numa linha de altura fixa.
 *
 * A versão anterior usava a barra grande do painel e repetia o mesmo
 * número duas vezes por célula — cada linha ficava com 78 px de altura e
 * caberiam sete processos na tela. Aqui cabem trinta, que é o ponto de
 * uma lista de processos.
 */
function CelulaPct({ valor }) {
  const v = Math.max(0, valor || 0);
  const largura = Math.min(v, 100);
  return (
    <td className="celula-pct">
      <span className="mono">{v.toFixed(1)}%</span>
      <div className="meter meter-fino">
        <div className={`meter-fill meter-${nivel(largura)}`} style={{ width: `${largura}%` }} />
      </div>
    </td>
  );
}

function Th({ campo, ordem, onClick, children, className, style }) {
  const ativo = ordem.campo === campo;
  const seta = ativo ? (ordem.dir === "asc" ? " ▲" : " ▼") : "";
  return (
    <th
      className={className}
      style={{ ...(style || {}), cursor: "pointer", userSelect: "none", whiteSpace: "nowrap" }}
      onClick={() => onClick(campo)}
      title={t("Ordenar por esta coluna")}
    >
      {children}
      <span style={{ color: ativo ? "var(--blue)" : "var(--text-3)" }}>{seta || " ↕"}</span>
    </th>
  );
}

function Resumo({ d }) {
  const load = d.load || [0, 0, 0];
  const cargaAlta = d.load_por_nucleo >= 1;
  return (
    <div className="grid-stats">
      <div className="card card-tight stat">
        <span className="stat-label">{t("Processador")}</span>
        <div className="stat-value" style={{ color: d.cpu_pct >= 90 ? "var(--red)" : d.cpu_pct >= 75 ? "var(--amber)" : "var(--green)" }}>
          {d.cpu_pct}%
        </div>
        <span className="stat-sub">
          {d.cpu_detalhe && d.cpu_detalhe.wa ? `${d.cpu_detalhe.wa}% esperando disco` : "ocupação total agora"}
        </span>
      </div>

      <div className="card card-tight stat">
        <span className="stat-label">{t("Carga por núcleo")}</span>
        <div className="stat-value" style={{ color: cargaAlta ? "var(--amber)" : "var(--green)" }}>
          {d.load_por_nucleo}
        </div>
        <span className="stat-sub">
          {load.map((x) => x.toFixed(2)).join(" / ")} em {d.nucleos} núcleo(s) — 1, 5 e 15 min
        </span>
      </div>

      <div className="card card-tight stat">
        <span className="stat-label">{t("Memória")}</span>
        <div className="stat-value" style={{ color: d.mem.pct >= 90 ? "var(--red)" : d.mem.pct >= 75 ? "var(--amber)" : "var(--green)" }}>
          {d.mem.pct}%
        </div>
        <span className="stat-sub">
          {formatBytes(d.mem.usado)} de {formatBytes(d.mem.total)} usados
        </span>
      </div>

      <div className="card card-tight stat">
        <span className="stat-label">{t("Tarefas / Swap")}</span>
        <div className="stat-value">{d.tarefas.total || "—"}</div>
        <span className="stat-sub">
          {d.tarefas.rodando || 0} rodando ·{" "}
          {d.swap.total ? `swap ${d.swap.pct}%` : "sem swap"}
        </span>
      </div>
    </div>
  );
}
