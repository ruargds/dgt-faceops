import React, { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatData } from "../../api";
import { Carregando, Erro, Medidor, SeletorHost, Vazio, useHosts } from "../Comuns";
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
/**
 * Licenciamento do FindFace.
 *
 * "Cabem quantas câmeras ainda?" era pergunta que só a interface da
 * NtechLab respondia, e que aparece em toda conversa de expansão. Vem da
 * API HTTP: limite, uso e o que sobra, com o número real de câmeras
 * cadastradas confrontando o limite licenciado.
 *
 * Quando a instalação devolve a licença num formato que o achatamento do
 * servidor não reconhece, o corpo bruto fica acessível ali mesmo — melhor
 * mostrar JSON do que esconder o dado.
 */
function Licenciamento({ dados, erro }) {
  const [verBruto, setVerBruto] = useState(false);

  if (erro) {
    return (
      <div className="card card-tight">
        <div className="section-title" style={{ marginBottom: 4 }}>
          Licenciamento
        </div>
        <div className="small muted">{erro}</div>
      </div>
    );
  }
  if (!dados) return null;

  const itens = dados.itens || [];

  return (
    <div className="card">
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 4 }}>
        <div className="section-title">Licenciamento</div>
        <span className="small muted mono">
          {dados.url}
          {dados.caminho}
        </span>
      </div>

      {itens.length === 0 ? (
        <div className="small muted">
          A licença respondeu, mas nenhum limite reconhecível veio no corpo.
          Veja o conteúdo bruto abaixo.
        </div>
      ) : (
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table>
            <thead>
              <tr>
                <th>Recurso</th>
                <th className="right">Liberado</th>
                <th className="right">Em uso</th>
                <th className="right">Livre</th>
                <th style={{ width: 160 }}>Ocupação</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((i, idx) => {
                const pct =
                  i.limite && i.limite > 0 && i.usado !== null && i.usado !== undefined
                    ? (i.usado / i.limite) * 100
                    : null;
                return (
                  <tr key={`${i.recurso}-${idx}`}>
                    <td className="mono">{i.recurso}</td>
                    <td className="right mono">
                      {i.ilimitado ? "ilimitado" : i.limite === null ? "—" : i.limite.toLocaleString("pt-BR")}
                    </td>
                    <td className="right mono">
                      {i.usado === null || i.usado === undefined
                        ? "—"
                        : i.usado.toLocaleString("pt-BR")}
                    </td>
                    <td className="right mono">
                      {i.restante === null || i.restante === undefined
                        ? "—"
                        : i.restante.toLocaleString("pt-BR")}
                    </td>
                    <td>
                      {pct === null ? (
                        <span className="small muted">—</span>
                      ) : (
                        <div className="stack-h" style={{ gap: 8 }}>
                          <Medidor pct={pct} />
                          <span className="small mono">{pct.toFixed(0)}%</span>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="stack-h" style={{ marginTop: 10 }}>
        <span className="small muted" style={{ flex: 1 }}>
          {dados.cameras_cadastradas === null || dados.cameras_cadastradas === undefined
            ? "Contagem de câmeras indisponível pela API."
            : `${dados.cameras_cadastradas} câmera(s) cadastrada(s) no servidor agora.`}
        </span>
        <button className="btn btn-ghost btn-sm" onClick={() => setVerBruto((v) => !v)}>
          {verBruto ? "Esconder resposta bruta" : "Ver resposta bruta"}
        </button>
      </div>

      {verBruto && (
        <pre
          className="mono small"
          style={{ marginTop: 8, maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap" }}
        >
          {JSON.stringify(dados.bruto, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function DispositivosView() {
  const { hosts, hostId, setHostId, carregando: carregandoHosts } = useHosts();
  const [dados, setDados] = useState(null);
  const [periodo, setPeriodo] = useState("dia");
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [filtro, setFiltro] = useState("");
  const [licenca, setLicenca] = useState(null);
  const [erroLicenca, setErroLicenca] = useState("");

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

  // Licença é leitura barata (um GET, sem SSH e sem varrer evento), então
  // carrega sozinha ao trocar de servidor — diferente da contagem de
  // eventos, que continua sob demanda. Assim a tela responde algo útil
  // antes de alguém clicar em Consultar.
  useEffect(() => {
    if (!hostId) return;
    let vivo = true;
    setLicenca(null);
    setErroLicenca("");
    api
      .licencaFindFace(hostId)
      .then((r) => vivo && setLicenca(r))
      .catch((ex) => vivo && setErroLicenca(ex.message));
    return () => {
      vivo = false;
    };
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
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() =>
                api
                  .baixar(api.urlExportarDispositivos(hostId, periodo), `cameras-${periodo}.csv`)
                  .catch((e) => setErro(e.message))
              }
              title="Baixar em CSV"
            >
              <IconDownload size={15} /> Exportar
            </button>
          )}
        </div>
      </div>

      <Erro mensagem={erro} onTentar={consultar} />

      <Licenciamento dados={licenca} erro={erroLicenca} />

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
