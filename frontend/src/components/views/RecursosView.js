import React, { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatDuracao, nivel } from "../../api";
import {
  Carregando,
  Erro,
  Estatistica,
  Medidor,
  SeletorHost,
  Vazio,
  useHosts,
} from "../Comuns";
import { IconAtualizar, IconGPU } from "../Icons";

export default function RecursosView() {
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [coletadoEm, setColetadoEm] = useState(null);

  // Coleta SOB DEMANDA. Sem polling automático: o painel bate SSH no
  // servidor de produção a cada leitura, e ficar fazendo isso de minuto
  // em minuto só rouba CPU de quem está reconhecendo rosto.
  const coletar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    try {
      const resposta = await api.metricas(hostId);
      setDados(resposta);
      setColetadoEm(new Date());
    } catch (ex) {
      setErro(ex.message);
      setDados(null);
    } finally {
      setCarregando(false);
    }
  }, [hostId]);

  useEffect(() => {
    if (hostId) coletar();
  }, [hostId, coletar]);

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo="Cadastre um servidor primeiro" />;

  const mem = dados && dados.memoria;
  const cpu = dados && dados.cpu;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">Recursos</div>
          <div className="page-sub">
            Leitura direta da máquina no momento do clique — sem Zabbix, sem histórico
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <button className="btn btn-primary" onClick={coletar} disabled={carregando || !hostId}>
            <IconAtualizar size={15} /> {carregando ? "Coletando…" : "Atualizar"}
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={coletar} />

      {carregando && !dados && <Carregando texto="Lendo a máquina…" />}

      {dados && (
        <div className="stack-v">
          <div className="small muted">
            Coletado em {coletadoEm ? coletadoEm.toLocaleTimeString("pt-BR") : "—"} ·
            leitura levou {dados.coleta_ms} ms · máquina de pé há{" "}
            {formatDuracao(dados.uptime_segundos)}
          </div>

          <div className="grid-stats">
            <Estatistica
              rotulo="Memória em uso"
              valor={formatBytes(mem.usado_bytes)}
              sub={`${formatBytes(mem.disponivel_bytes)} disponíveis de ${formatBytes(mem.total_bytes)}`}
              pct={mem.percentual}
            />
            <Estatistica
              rotulo="Cache e buffers"
              valor={formatBytes(mem.cache_bytes + mem.buffers_bytes)}
              sub="Não conta como uso — o kernel devolve quando precisar"
            />
            {mem.swap_total_bytes > 0 && (
              <Estatistica
                rotulo="Swap"
                valor={formatBytes(mem.swap_usado_bytes)}
                sub={`de ${formatBytes(mem.swap_total_bytes)}`}
                pct={mem.swap_percentual}
              />
            )}
            <Estatistica
              rotulo="Carga por núcleo"
              valor={cpu.carga_por_nucleo.toFixed(2)}
              sub={`${cpu.carga_1min} / ${cpu.carga_5min} / ${cpu.carga_15min} em ${cpu.nucleos} núcleos`}
              pct={Math.min(cpu.carga_por_nucleo * 100, 100)}
            />
          </div>

          {dados.gpus.length > 0 && (
            <div>
              <div className="section-title">
                <span className="stack-h">
                  <IconGPU size={14} /> GPU
                </span>
              </div>
              <div className="grid-cards">
                {dados.gpus.map((g) => (
                  <div className="card" key={g.indice}>
                    <div style={{ fontWeight: 600, color: "var(--navy)", marginBottom: 10 }}>
                      GPU {g.indice} · {g.nome}
                    </div>
                    <div className="stack-v" style={{ gap: 10 }}>
                      <LinhaMedidor
                        rotulo="Utilização"
                        valor={g.utilizacao_pct != null ? `${g.utilizacao_pct}%` : "n/d"}
                        pct={g.utilizacao_pct}
                      />
                      <LinhaMedidor
                        rotulo="Memória de vídeo"
                        valor={`${formatBytes(g.memoria_usada_bytes)} / ${formatBytes(g.memoria_total_bytes)}`}
                        pct={g.memoria_pct}
                      />
                      <div className="stack-h small muted" style={{ gap: 14 }}>
                        {g.temperatura_c != null && <span>{g.temperatura_c} °C</span>}
                        {g.potencia_w != null && (
                          <span>
                            {g.potencia_w} W
                            {g.potencia_limite_w ? ` / ${g.potencia_limite_w} W` : ""}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {dados.gpu_processos.length > 0 && (
                <div className="table-wrap" style={{ marginTop: 12 }}>
                  <table>
                    <thead>
                      <tr>
                        <th>PID</th>
                        <th>Processo usando a GPU</th>
                        <th className="right">Memória de vídeo</th>
                      </tr>
                    </thead>
                    <tbody>
                      {dados.gpu_processos.map((p, i) => (
                        <tr key={i}>
                          <td className="mono">{p.pid}</td>
                          <td className="mono">{p.processo}</td>
                          <td className="right mono">{formatBytes(p.memoria_bytes)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          <div>
            <div className="section-title">Discos</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Ponto de montagem</th>
                    <th>Dispositivo</th>
                    <th className="right">Usado</th>
                    <th className="right">Livre</th>
                    <th className="right">Total</th>
                    <th style={{ width: 150 }}>Ocupação</th>
                  </tr>
                </thead>
                <tbody>
                  {dados.discos.map((d) => (
                    <tr key={d.ponto}>
                      <td className="mono">{d.ponto}</td>
                      <td className="mono muted small">{d.dispositivo}</td>
                      <td className="right mono">{formatBytes(d.usado_bytes)}</td>
                      <td className="right mono">{formatBytes(d.livre_bytes)}</td>
                      <td className="right mono">{formatBytes(d.total_bytes)}</td>
                      <td>
                        <div className="stack-h" style={{ gap: 8 }}>
                          <div style={{ flex: 1 }}>
                            <Medidor pct={d.percentual} />
                          </div>
                          <span
                            className="small mono"
                            style={{
                              color:
                                nivel(d.percentual) === "err"
                                  ? "var(--red)"
                                  : nivel(d.percentual) === "warn"
                                  ? "var(--amber)"
                                  : "var(--text-2)",
                              minWidth: 40,
                              textAlign: "right",
                            }}
                          >
                            {d.percentual}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {dados.containers.length > 0 && (
            <div>
              <div className="section-title">
                Containers por consumo de memória (10 maiores)
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Container</th>
                      <th className="right">CPU</th>
                      <th className="right">Memória</th>
                      <th className="right">Limite</th>
                      <th className="right">PIDs</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dados.containers.slice(0, 10).map((c) => (
                      <tr key={c.id || c.nome}>
                        <td className="mono">{c.nome}</td>
                        <td className="right mono">{c.cpu_pct.toFixed(1)}%</td>
                        <td className="right mono">{formatBytes(c.memoria_bytes)}</td>
                        <td className="right mono muted">
                          {c.memoria_limite_bytes ? formatBytes(c.memoria_limite_bytes) : "—"}
                        </td>
                        <td className="right mono muted">{c.pids}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <AnaliseArmazenamento hostId={hostId} />
        </div>
      )}
    </>
  );
}

function LinhaMedidor({ rotulo, valor, pct }) {
  return (
    <div>
      <div className="stat-top" style={{ marginBottom: 4 }}>
        <span className="small muted">{rotulo}</span>
        <span className="small mono">{valor}</span>
      </div>
      <Medidor pct={pct || 0} />
    </div>
  );
}

/**
 * Onde o disco do FindFace está sendo gasto.
 *
 * Fica atrás de um botão próprio porque `du` numa árvore com milhões de
 * fotos de evento leva minutos — não pode entrar na coleta rápida.
 */
function AnaliseArmazenamento({ hostId }) {
  const [dados, setDados] = useState(null);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState("");

  useEffect(() => {
    setDados(null);
    setErro("");
  }, [hostId]);

  async function analisar() {
    setCarregando(true);
    setErro("");
    try {
      setDados(await api.armazenamento(hostId));
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }

  return (
    <div className="card">
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 12 }}>
        <div>
          <div className="section-title" style={{ marginBottom: 2 }}>
            Onde o disco do FindFace está sendo gasto
          </div>
          <div className="small muted">
            Varre <span className="mono">/opt/findface-multi/data</span>. Leva alguns
            minutos em servidor com muitos eventos.
          </div>
        </div>
        <button className="btn btn-secondary" onClick={analisar} disabled={carregando}>
          {carregando ? "Analisando…" : "Analisar"}
        </button>
      </div>

      <Erro mensagem={erro} />

      {carregando && <Carregando texto="Somando diretórios no servidor…" />}

      {dados && (
        <>
          <div className="small muted" style={{ marginBottom: 10 }}>
            Total em <span className="mono">{dados.base}</span>:{" "}
            <strong>{formatBytes(dados.total_bytes)}</strong>
            {dados.parcial && " (leitura parcial — houve timeout em parte da árvore)"}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Diretório</th>
                  <th className="right">Tamanho</th>
                  <th style={{ width: 160 }}>Fatia</th>
                </tr>
              </thead>
              <tbody>
                {dados.itens.slice(0, 20).map((item) => {
                  const fatia = dados.total_bytes
                    ? (item.bytes / dados.total_bytes) * 100
                    : 0;
                  return (
                    <tr key={item.caminho}>
                      <td className="mono small">{item.caminho}</td>
                      <td className="right mono">{formatBytes(item.bytes)}</td>
                      <td>
                        <div className="stack-h" style={{ gap: 8 }}>
                          <div style={{ flex: 1 }}>
                            <Medidor pct={fatia} />
                          </div>
                          <span className="small mono muted" style={{ minWidth: 38, textAlign: "right" }}>
                            {fatia.toFixed(0)}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
