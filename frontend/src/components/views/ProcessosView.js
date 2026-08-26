import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes } from "../../api";
import { BarraMetrica } from "../Graficos";
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
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [ordenarPor, setOrdenarPor] = useState("cpu"); // cpu | mem
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

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo="Cadastre um servidor primeiro" />;

  const processos = dados
    ? [...dados.processos].sort((a, b) =>
        ordenarPor === "mem" ? b.mem - a.mem : b.cpu - a.cpu
      )
    : [];

  return (
    <>
      <div className="page-head" style={{ marginBottom: 14 }}>
        <div>
          <div className="page-title">Processos ao vivo</div>
          <div className="page-sub">
            Quem está usando a máquina agora — como o htop, só que explicado
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
          <button className="btn btn-secondary btn-sm" onClick={carregar} title="Atualizar agora">
            <IconAtualizar size={14} />
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {!dados && !erro && <Carregando texto="Lendo os processos do servidor…" />}

      {dados && (
        <>
          <Resumo d={dados} />

          <div className="card" style={{ marginTop: 16 }}>
            <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 8 }}>
              <div className="section-title" style={{ marginBottom: 0 }}>
                Processos que mais consomem
              </div>
              <div className="stack-h" style={{ gap: 6 }}>
                <button
                  className={`btn btn-sm ${ordenarPor === "cpu" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setOrdenarPor("cpu")}
                >
                  por CPU
                </button>
                <button
                  className={`btn btn-sm ${ordenarPor === "mem" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setOrdenarPor("mem")}
                >
                  por memória
                </button>
              </div>
            </div>

            <div className="small muted" style={{ marginBottom: 10 }}>
              <strong>%CPU</strong> é o quanto de um núcleo o processo usa agora
              (pode passar de 100% se usa vários). <strong>%MEM</strong> é a
              fatia da memória física. <strong>Tempo</strong> é quanto de CPU ele
              já acumulou desde que iniciou.
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="right">PID</th>
                    <th>Usuário</th>
                    <th style={{ width: 150 }}>CPU</th>
                    <th style={{ width: 150 }}>Memória</th>
                    <th className="right">Tempo</th>
                    <th>Programa</th>
                  </tr>
                </thead>
                <tbody>
                  {processos.map((p) => (
                    <tr key={p.pid}>
                      <td className="right mono small">{p.pid}</td>
                      <td className="small">{p.usuario}</td>
                      <td>
                        <BarraMetrica rotulo="" valor={Math.min(p.cpu, 100)} limite={101} unidade="%" detalhe={`${p.cpu.toFixed(1)}%`} />
                      </td>
                      <td>
                        <BarraMetrica rotulo="" valor={Math.min(p.mem, 100)} limite={101} unidade="%" detalhe={`${p.mem.toFixed(1)}%`} />
                      </td>
                      <td className="right mono small">{p.tempo}</td>
                      <td className="mono small" style={{ maxWidth: 320, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={p.comando}>
                        {p.comando}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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

function Resumo({ d }) {
  const load = d.load || [0, 0, 0];
  const cargaAlta = d.load_por_nucleo >= 1;
  return (
    <div className="grid-stats">
      <div className="card card-tight stat">
        <span className="stat-label">Processador</span>
        <div className="stat-value" style={{ color: d.cpu_pct >= 90 ? "var(--red)" : d.cpu_pct >= 75 ? "var(--amber)" : "var(--green)" }}>
          {d.cpu_pct}%
        </div>
        <span className="stat-sub">
          {d.cpu_detalhe && d.cpu_detalhe.wa ? `${d.cpu_detalhe.wa}% esperando disco` : "ocupação total agora"}
        </span>
      </div>

      <div className="card card-tight stat">
        <span className="stat-label">Carga por núcleo</span>
        <div className="stat-value" style={{ color: cargaAlta ? "var(--amber)" : "var(--green)" }}>
          {d.load_por_nucleo}
        </div>
        <span className="stat-sub">
          {load.map((x) => x.toFixed(2)).join(" / ")} em {d.nucleos} núcleo(s) — 1, 5 e 15 min
        </span>
      </div>

      <div className="card card-tight stat">
        <span className="stat-label">Memória</span>
        <div className="stat-value" style={{ color: d.mem.pct >= 90 ? "var(--red)" : d.mem.pct >= 75 ? "var(--amber)" : "var(--green)" }}>
          {d.mem.pct}%
        </div>
        <span className="stat-sub">
          {formatBytes(d.mem.usado)} de {formatBytes(d.mem.total)} usados
        </span>
      </div>

      <div className="card card-tight stat">
        <span className="stat-label">Tarefas / Swap</span>
        <div className="stat-value">{d.tarefas.total || "—"}</div>
        <span className="stat-sub">
          {d.tarefas.rodando || 0} rodando ·{" "}
          {d.swap.total ? `swap ${d.swap.pct}%` : "sem swap"}
        </span>
      </div>
    </div>
  );
}
