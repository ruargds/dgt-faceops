import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes, formatDuracao, nivel } from "../../api";
import { t } from "../../i18n";
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

export default function RecursosView({ alvo }) {
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [coletadoEm, setColetadoEm] = useState(null);
  const [aoVivo, setAoVivo] = useState(false);
  const [intervalo, setIntervalo] = useState(15);

  // Chegou de um atalho de alerta ("ir para Recursos") — abre já no host
  // que estava com carga/swap alta, em vez do primeiro host ativo.
  useEffect(() => {
    if (alvo && alvo.hostId) setHostId(alvo.hostId);
  }, [alvo, setHostId]);

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

  // Acompanhamento ao vivo.
  //
  // Três garantias para não onerar nada: para ao sair da tela (cleanup do
  // efeito), para quando a aba deixa de estar visível (Page Visibility), e
  // nunca dispara uma coleta com outra em curso. Cada coleta é um SSH no
  // servidor de produção — deixar isso rodando esquecido em segundo plano
  // seria exatamente o tipo de peso que o painel promete não criar.
  const refCarregando = useRef(false);
  refCarregando.current = carregando;

  useEffect(() => {
    if (!aoVivo || !hostId) return undefined;

    let vivo = true;
    let timer = null;

    const tick = async () => {
      if (!vivo) return;
      if (document.visibilityState === "visible" && !refCarregando.current) {
        await coletar();
      }
      if (vivo) timer = setTimeout(tick, intervalo * 1000);
    };

    timer = setTimeout(tick, intervalo * 1000);

    const aoTrocarVisibilidade = () => {
      if (document.visibilityState === "visible" && vivo && !refCarregando.current) {
        coletar();
      }
    };
    document.addEventListener("visibilitychange", aoTrocarVisibilidade);

    return () => {
      vivo = false;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", aoTrocarVisibilidade);
    };
  }, [aoVivo, hostId, intervalo, coletar]);

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo={t("Cadastre um servidor primeiro")} />;

  const mem = dados && dados.memoria;
  const cpu = dados && dados.cpu;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.recursos")}</div>
          <div className="page-sub">
            {t("tela.recursos.sub")}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          {aoVivo && (
            <select
              value={intervalo}
              onChange={(e) => setIntervalo(Number(e.target.value))}
              style={{ width: "auto" }}
              title={t("Intervalo entre coletas")}
            >
              <option value={10}>{t("10s")}</option>
              <option value={15}>{t("15s")}</option>
              <option value={30}>{t("30s")}</option>
              <option value={60}>{t("60s")}</option>
            </select>
          )}
          <button
            className={`btn ${aoVivo ? "btn-danger" : "btn-secondary"}`}
            onClick={() => setAoVivo((a) => !a)}
            disabled={!hostId}
            title={
              aoVivo
                ? "Para de coletar"
                : "Coleta a cada intervalo enquanto esta tela estiver aberta e visível"
            }
          >
            {aoVivo ? "Parar acompanhamento" : "Acompanhar ao vivo"}
          </button>
          <button className="btn btn-primary" onClick={coletar} disabled={carregando || !hostId}>
            <IconAtualizar size={15} /> {carregando ? "Coletando…" : "Atualizar"}
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={coletar} />

      {carregando && !dados && <Carregando texto={t("Lendo a máquina…")} />}

      {dados && (
        <div className="stack-v">
          <div className="stack-h small muted">
            <span>
              Coletado em {coletadoEm ? coletadoEm.toLocaleTimeString("pt-BR") : "—"} ·
              leitura levou {dados.coleta_ms} ms · máquina de pé há{" "}
              {formatDuracao(dados.uptime_segundos)}
            </span>
            {aoVivo && (
              <span className="pill pill-ok" title={t("Para sozinho ao sair da tela ou trocar de aba")}>
                ao vivo · {intervalo}s
              </span>
            )}
          </div>

          <div className="grid-stats">
            <Estatistica
              rotulo={t("Memória em uso")}
              valor={formatBytes(mem.usado_bytes)}
              sub={`${formatBytes(mem.disponivel_bytes)} disponíveis de ${formatBytes(mem.total_bytes)}`}
              pct={mem.percentual}
            />
            <Estatistica
              rotulo={t("Cache e buffers")}
              valor={formatBytes(mem.cache_bytes + mem.buffers_bytes)}
              sub={t("Não conta como uso — o kernel devolve quando precisar")}
            />
            {mem.swap_total_bytes > 0 && (
              <Estatistica
                rotulo={t("Swap")}
                valor={formatBytes(mem.swap_usado_bytes)}
                sub={`de ${formatBytes(mem.swap_total_bytes)}`}
                pct={mem.swap_percentual}
              />
            )}
            {/* Uso e carga respondem perguntas diferentes, e por isso são
                dois cartões. Uso é ocupação: quanto da CPU foi gasta.
                Carga é fila: quantos processos querem CPU. Máquina com
                carga 4 e uso 20% está esperando disco, não CPU — trocar
                de servidor por causa disso é dinheiro no lixo. */}
            <Estatistica
              rotulo={t("Processador em uso")}
              valor={cpu.uso_pct === null || cpu.uso_pct === undefined ? "—" : `${cpu.uso_pct}%`}
              sub={
                cpu.detalhe
                  ? `usuário ${cpu.detalhe.usuario}% · sistema ${cpu.detalhe.sistema}% · ` +
                    `espera disco ${cpu.detalhe.espera_io}%` +
                    (cpu.detalhe.roubado > 0 ? ` · roubado ${cpu.detalhe.roubado}%` : "")
                  : "leitura de /proc/stat indisponível"
              }
              pct={cpu.uso_pct === null || cpu.uso_pct === undefined ? undefined : cpu.uso_pct}
            />
            <Estatistica
              rotulo={t("Carga por núcleo")}
              valor={cpu.carga_por_nucleo.toFixed(2)}
              sub={`${cpu.carga_1min} / ${cpu.carga_5min} / ${cpu.carga_15min} em ${cpu.nucleos} núcleos`}
              pct={Math.min(cpu.carga_por_nucleo * 100, 100)}
            />
          </div>

          {cpu.por_nucleo && cpu.por_nucleo.length > 1 && cpu.por_nucleo.length <= 32 && (
            <div>
              <div className="section-title">{t("Uso por núcleo")}</div>
              <div className="grid-nucleos">
                {cpu.por_nucleo.map((n) => (
                  <div className="nucleo" key={n.nucleo}>
                    <div className="nucleo-topo">
                      <span className="mono small">#{n.nucleo}</span>
                      <span className="mono small" style={{ fontWeight: 600 }}>
                        {n.uso_pct}%
                      </span>
                    </div>
                    <Medidor pct={n.uso_pct} />
                  </div>
                ))}
              </div>
              <div className="small muted" style={{ marginTop: 6 }}>
                Um núcleo cravado em 100% com os outros parados é processo de uma
                thread só — mais CPU não resolve, e o gargalo está no programa.
              </div>
            </div>
          )}

          {dados.gpus.length > 0 && (
            <div>
              <div className="section-title">
                <span className="stack-h">
                  <IconGPU size={14} /> {t("GPU")}</span>
              </div>
              <div className="grid-cards">
                {dados.gpus.map((g) => (
                  <div className="card" key={g.indice}>
                    <div style={{ fontWeight: 600, color: "var(--titulo)", marginBottom: 10 }}>
                      GPU {g.indice} · {g.nome}
                    </div>
                    <div className="stack-v" style={{ gap: 10 }}>
                      <LinhaMedidor
                        rotulo={t("Utilização")}
                        valor={g.utilizacao_pct != null ? `${g.utilizacao_pct}%` : "n/d"}
                        pct={g.utilizacao_pct}
                      />
                      <LinhaMedidor
                        rotulo={t("Memória de vídeo")}
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
                        <th>{t("PID")}</th>
                        <th>{t("Processo usando a GPU")}</th>
                        <th className="right">{t("Memória de vídeo")}</th>
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
            <div className="section-title">{t("Discos")}</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("Ponto de montagem")}</th>
                    <th>{t("Dispositivo")}</th>
                    <th className="right">{t("Usado")}</th>
                    <th className="right">{t("Livre")}</th>
                    <th className="right">{t("Total")}</th>
                    <th style={{ width: 150 }}>{t("Ocupação")}</th>
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
                      <th>{t("Container")}</th>
                      <th className="right">{t("CPU")}</th>
                      <th className="right">{t("Memória")}</th>
                      <th className="right">{t("Limite")}</th>
                      <th className="right">{t("PIDs")}</th>
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
          <div className="section-title" style={{ marginBottom: 2 }}>{t("Onde o disco do FindFace está sendo gasto")}</div>
          <div className="small muted">{t("Varre")} <span className="mono">/opt/findface-multi/data</span>. Leva alguns
            minutos em servidor com muitos eventos.
          </div>
        </div>
        <button className="btn btn-secondary" onClick={analisar} disabled={carregando}>
          {carregando ? "Analisando…" : "Analisar"}
        </button>
      </div>

      <Erro mensagem={erro} />

      {carregando && <Carregando texto={t("Somando diretórios no servidor…")} />}

      {dados && (
        <>
          <div className="small muted" style={{ marginBottom: 10 }}>{t("Total em")} <span className="mono">{dados.base}</span>:{" "}
            <strong>{formatBytes(dados.total_bytes)}</strong>
            {dados.parcial && " (leitura parcial — houve timeout em parte da árvore)"}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("Diretório")}</th>
                  <th className="right">{t("Tamanho")}</th>
                  <th style={{ width: 160 }}>{t("Fatia")}</th>
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
