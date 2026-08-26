import React, { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatData } from "../../api";
import { Carregando, Erro, SeletorHost, Vazio, useHosts } from "../Comuns";
import { BarraMetrica } from "../Graficos";
import { IconAtualizar, IconDownload, IconAlerta, IconOk } from "../Icons";

const PERIODOS = [
  { id: "hora", rotulo: "1 hora" },
  { id: "dia", rotulo: "24 horas" },
  { id: "semana", rotulo: "7 dias" },
  { id: "mes", rotulo: "30 dias" },
];

/**
 * Câmeras cadastradas no FindFace: quantas, quando falaram, quanto geram.
 *
 * Consulta pesada e sob demanda — lê o banco do FindFace e agrega. Nunca
 * fica atualizando sozinha: contar evento a cada minuto seria o peso que
 * o painel promete não criar.
 */
export default function DispositivosView() {
  const { hosts, hostId, setHostId, carregando: carregandoHosts } = useHosts();
  const [dados, setDados] = useState(null);
  const [periodo, setPeriodo] = useState("dia");
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [filtro, setFiltro] = useState("");

  const consultar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    try {
      setDados(await api.dispositivos(hostId, periodo));
    } catch (ex) {
      setErro(ex.message);
      setDados(null);
    } finally {
      setCarregando(false);
    }
  }, [hostId, periodo]);

  useEffect(() => {
    setDados(null);
  }, [hostId]);

  if (carregandoHosts) return <Carregando />;
  if (!hosts.length) return <Vazio titulo="Cadastre um servidor primeiro" />;

  const cameras = dados
    ? dados.cameras.filter((c) =>
        !filtro || c.nome.toLowerCase().includes(filtro.toLowerCase())
      )
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">Câmeras</div>
          <div className="page-sub">
            Dispositivos cadastrados, última comunicação e volume de eventos
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <select value={periodo} onChange={(e) => setPeriodo(e.target.value)} style={{ width: "auto" }}>
            {PERIODOS.map((p) => (
              <option key={p.id} value={p.id}>{p.rotulo}</option>
            ))}
          </select>
          <button className="btn btn-primary" onClick={consultar} disabled={carregando || !hostId}>
            <IconAtualizar size={15} /> {carregando ? "Consultando…" : "Consultar"}
          </button>
          {dados && (
            <a
              className="btn btn-secondary"
              href={api.urlExportarDispositivos(hostId, periodo)}
              title="Baixar em CSV"
            >
              <IconDownload size={15} /> Exportar
            </a>
          )}
        </div>
      </div>

      <Erro mensagem={erro} onTentar={consultar} />

      {!dados && !carregando && (
        <Vazio titulo="Clique em Consultar">
          Lê o banco do FindFace e conta os eventos por câmera. Pode levar alguns
          segundos em base grande — por isso é sob demanda, não automático.
        </Vazio>
      )}

      {carregando && !dados && <Carregando texto="Contando eventos no banco do FindFace…" />}

      {dados && (
        <div className="stack-v">
          <div className="grid-stats">
            <div className="card card-tight stat">
              <span className="stat-label">Câmeras cadastradas</span>
              <div className="stat-value">{dados.total_cameras}</div>
              <span className="stat-sub">{dados.esquema.banco}</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">Comunicando ({dados.periodo_rotulo})</span>
              <div className="stat-value" style={{ color: "var(--green)" }}>
                {dados.cameras_com_evento}
              </div>
              <span className="stat-sub">geraram ao menos um evento</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">Sem eventos</span>
              <div
                className="stat-value"
                style={{ color: dados.cameras_mudas > 0 ? "var(--amber)" : "var(--text-3)" }}
              >
                {dados.cameras_mudas}
              </div>
              <span className="stat-sub">nada no período — pode estar offline</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">Total de eventos</span>
              <div className="stat-value">{dados.total_eventos.toLocaleString("pt-BR")}</div>
              <span className="stat-sub">no período</span>
            </div>
          </div>

          <div className="stack-h" style={{ justifyContent: "space-between" }}>
            <input
              value={filtro}
              onChange={(e) => setFiltro(e.target.value)}
              placeholder="filtrar câmera pelo nome…"
              style={{ maxWidth: 280 }}
            />
            {dados.estimativa && (
              <span className="small muted">
                Volume por câmera é estimativa (rateio pela participação nos eventos).
              </span>
            )}
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Câmera</th>
                  <th>Situação</th>
                  <th className="right">Eventos ({dados.periodo_rotulo})</th>
                  <th style={{ width: 140 }}>Participação</th>
                  <th className="right">Volume estimado</th>
                  <th>Último evento</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map((c) => {
                  const muda = c.eventos === 0;
                  return (
                    <tr key={c.id}>
                      <td>
                        <div style={{ fontWeight: 500 }}>{c.nome}</div>
                        <div className="small muted mono">
                          id {c.id}{c.grupo ? ` · grupo ${c.grupo}` : ""}
                        </div>
                      </td>
                      <td>
                        {muda ? (
                          <span className="pill pill-warn">
                            <IconAlerta size={11} /> sem eventos
                          </span>
                        ) : (
                          <span className="pill pill-ok">
                            <IconOk size={11} /> ativa
                          </span>
                        )}
                      </td>
                      <td className="right mono">{c.eventos.toLocaleString("pt-BR")}</td>
                      <td>
                        <BarraMetrica rotulo="" valor={c.fatia_pct} limite={101} unidade="%" />
                      </td>
                      <td className="right mono small">
                        {c.bytes_estimados ? formatBytes(c.bytes_estimados) : "—"}
                      </td>
                      <td className="small">
                        {c.ultimo_evento ? (
                          formatData(c.ultimo_evento)
                        ) : (
                          <span className="muted">nunca</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="small muted">
            Fonte: banco <span className="mono">{dados.esquema.banco}</span>,
            tabelas de evento{" "}
            <span className="mono">{dados.esquema.tabelas_eventos.join(", ")}</span>.
            O esquema é descoberto automaticamente — se o FindFace for atualizado e
            a consulta falhar, use o botão de redescoberta.
          </div>
        </div>
      )}
    </>
  );
}
