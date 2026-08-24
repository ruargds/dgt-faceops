import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes, formatData, formatDuracao } from "../../api";
import { usePermissions } from "../../usePermissions";
import {
  Carregando,
  Erro,
  SeletorDestinos,
  SeletorHost,
  Selo,
  Vazio,
  useDestinos,
  useHosts,
} from "../Comuns";
import { IconAtualizar, IconBackup, IconDownload, IconLixeira, IconLogs } from "../Icons";

export const PERFIS = [
  {
    id: "config",
    nome: "Config",
    resumo: "configs/ + docker-compose + licença",
    detalhe:
      "Segundos, alguns MB, sem parar nada. Recupera a configuração do sistema, não os dados.",
  },
  {
    id: "essencial",
    nome: "Essencial",
    resumo: "config + PostgreSQL + Tarantool",
    detalhe:
      "Minutos, alguns GB, sem parar nada. Recupera cadastros, usuários, câmeras, dossiês e os vetores faciais. NÃO leva as fotos originais de evento. É o backup do dia a dia.",
  },
  {
    id: "completo",
    nome: "Completo",
    resumo: "procedimento oficial NtechLab — data/ inteiro",
    detalhe:
      "Horas, centenas de GB, PARA o FindFace Multi durante a cópia. Leva tudo, inclusive as fotos de evento. Use em janela de manutenção.",
  },
];

export default function BackupsView() {
  const { has } = usePermissions();
  const { hosts, erro: erroHosts, carregando: carregandoHosts } = useHosts(false);

  const [lista, setLista] = useState([]);
  const [filtroHost, setFiltroHost] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [novo, setNovo] = useState(false);
  const [detalhe, setDetalhe] = useState(null);
  const [espaco, setEspaco] = useState(null);

  const carregar = useCallback(async () => {
    setErro("");
    try {
      const params = filtroHost ? `?host_id=${filtroHost}` : "";
      const [runs, disco] = await Promise.all([
        api.backups(params),
        api.armazenamentoPainel().catch(() => null),
      ]);
      setLista(runs);
      if (disco) setEspaco(disco);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, [filtroHost]);

  useEffect(() => {
    carregar();
  }, [carregar]);

  // Enquanto houver execução em andamento, atualiza sozinho a cada 4s.
  // Fora disso não fica pedindo nada — a tela não precisa de polling
  // eterno para mostrar histórico parado.
  const temAndamento = lista.some((r) => r.status === "executando" || r.status === "pendente");
  const refCarregar = useRef(carregar);
  refCarregar.current = carregar;

  useEffect(() => {
    if (!temAndamento) return undefined;
    const id = setInterval(() => refCarregar.current(), 4000);
    return () => clearInterval(id);
  }, [temAndamento]);

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo="Cadastre um servidor primeiro" />;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">Backups</div>
          <div className="page-sub">
            Execuções sob demanda e disparadas por agendamento
            {espaco &&
              ` · ${formatBytes(espaco.livre_bytes)} livres no disco do painel`}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost
            hosts={hosts}
            hostId={filtroHost}
            onMudar={setFiltroHost}
            incluirTodos
          />
          <button className="btn btn-secondary" onClick={carregar}>
            <IconAtualizar size={15} /> Atualizar
          </button>
          {has("backups.run") && (
            <button className="btn btn-primary" onClick={() => setNovo(true)}>
              <IconBackup size={15} /> Novo backup
            </button>
          )}
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {carregando ? (
        <Carregando />
      ) : lista.length === 0 ? (
        <Vazio titulo="Nenhum backup ainda">
          Dispare um backup <strong>Essencial</strong> para validar o caminho de
          ponta a ponta antes de programar a recorrência.
        </Vazio>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Quando</th>
                <th>Servidor</th>
                <th>Perfil</th>
                <th>Situação</th>
                <th className="right">Tamanho</th>
                <th>Destinos</th>
                <th>Disparado por</th>
                <th style={{ width: 1 }}></th>
              </tr>
            </thead>
            <tbody>
              {lista.map((r) => (
                <LinhaBackup
                  key={r.id}
                  r={r}
                  onDetalhe={() => setDetalhe(r.id)}
                  onRemover={carregar}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {novo && (
        <ModalNovoBackup
          hosts={hosts.filter((h) => h.enabled)}
          onFechar={() => setNovo(false)}
          onPronto={async () => {
            setNovo(false);
            await carregar();
          }}
        />
      )}

      {detalhe && <ModalDetalhe runId={detalhe} onFechar={() => setDetalhe(null)} />}
    </>
  );
}

function LinhaBackup({ r, onDetalhe, onRemover }) {
  const { has } = usePermissions();
  const [removendo, setRemovendo] = useState(false);
  const emAndamento = r.status === "executando" || r.status === "pendente";

  async function remover() {
    if (!window.confirm(`Apagar o artefato de ${r.artifact_name || "esta execução"}?`)) return;
    setRemovendo(true);
    try {
      await api.removerBackup(r.id);
      await onRemover();
    } catch (ex) {
      window.alert(ex.message);
    } finally {
      setRemovendo(false);
    }
  }

  const duracao =
    r.finished_at && r.started_at
      ? (new Date(r.finished_at) - new Date(r.started_at)) / 1000
      : null;

  return (
    <tr>
      <td className="small">{formatData(r.started_at)}</td>
      <td className="mono small">{r.host_nome}</td>
      <td>
        <span className="pill pill-idle">{r.profile}</span>
        {r.caused_downtime && r.downtime_seconds > 0 && (
          <div className="small" style={{ color: "var(--amber)", marginTop: 3 }}>
            parou {formatDuracao(r.downtime_seconds)}
          </div>
        )}
      </td>
      <td style={{ minWidth: 190 }}>
        <Selo status={r.status} />
        {emAndamento && (
          <div style={{ marginTop: 5 }}>
            <div className="small muted" style={{ marginBottom: 3 }}>
              {r.stage} · {r.progress}%
            </div>
            <div className="progress">
              <div className="progress-fill" style={{ width: `${r.progress}%` }} />
            </div>
          </div>
        )}
        {r.status === "falha" && r.error && (
          <div className="small" style={{ color: "var(--red)", marginTop: 3, maxWidth: 280 }}>
            {r.error.slice(0, 130)}
          </div>
        )}
        {duracao != null && !emAndamento && (
          <div className="small muted" style={{ marginTop: 2 }}>
            levou {formatDuracao(duracao)}
          </div>
        )}
      </td>
      <td className="right mono small">{r.size_bytes ? formatBytes(r.size_bytes) : "—"}</td>
      <td>
        <div className="stack-h" style={{ gap: 4 }}>
          {(r.destinations || []).map((d, i) => (
            <span
              key={i}
              className={`pill ${d.status === "ok" ? "pill-ok" : "pill-err"}`}
              title={d.error || d.uri}
            >
              {d.nome || d.type}
            </span>
          ))}
          {(!r.destinations || r.destinations.length === 0) && (
            <span className="muted small">—</span>
          )}
        </div>
      </td>
      <td className="small muted">{r.triggered_by}</td>
      <td>
        <div className="stack-h" style={{ gap: 6, flexWrap: "nowrap" }}>
          <button className="btn btn-secondary btn-sm" onClick={onDetalhe} title="Ver log">
            <IconLogs size={14} />
          </button>
          {has("backups.download") && r.status === "sucesso" && !r.expired && (
            <a
              className="btn btn-secondary btn-sm"
              href={api.urlDownload(r.id)}
              title="Baixar artefato"
            >
              <IconDownload size={14} />
            </a>
          )}
          {has("backups.delete") && !r.expired && r.artifact_name && (
            <button
              className="btn btn-danger btn-sm"
              onClick={remover}
              disabled={removendo}
              title="Apagar artefato"
            >
              <IconLixeira size={14} />
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function ModalNovoBackup({ hosts, onFechar, onPronto }) {
  const { ativos, padroes, carregando: carregandoDest } = useDestinos();
  const [hostId, setHostId] = useState(hosts[0] ? hosts[0].id : null);
  const [perfil, setPerfil] = useState("essencial");
  const [destinos, setDestinos] = useState([]);
  const [tocou, setTocou] = useState(false);
  const [aceito, setAceito] = useState(false);
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  const host = hosts.find((h) => h.id === hostId);

  // Pre-seleciona os destinos padrao, mas so ate o operador mexer —
  // depois disso a escolha dele manda.
  useEffect(() => {
    if (!tocou && padroes.length) setDestinos(padroes);
  }, [padroes, tocou]);

  async function enviar(e) {
    e.preventDefault();
    if (!destinos.length) {
      setErro("Escolha pelo menos um destino.");
      return;
    }
    setErro("");
    setEnviando(true);
    try {
      await api.dispararBackup(hostId, {
        perfil,
        destinos,
        aceito_downtime: aceito,
      });
      await onPronto();
    } catch (ex) {
      setErro(ex.message);
      setEnviando(false);
    }
  }

  return (
    <div className="modal-bg" onClick={onFechar}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={enviar}>
        <div className="modal-head">
          <div className="modal-title">Novo backup</div>
        </div>
        <div className="modal-body">
          {erro && <div className="login-err">{erro}</div>}

          <div className="field">
            <label className="label label-required">Servidor</label>
            <select value={hostId ?? ""} onChange={(e) => setHostId(Number(e.target.value))}>
              {hosts.map((h) => (
                <option key={h.id} value={h.id}>{h.name}</option>
              ))}
            </select>
          </div>

          <div className="field">
            <label className="label label-required">Perfil</label>
            <div className="stack-v" style={{ gap: 8 }}>
              {PERFIS.map((p) => (
                <label
                  key={p.id}
                  className="card card-tight"
                  style={{
                    cursor: "pointer",
                    borderColor: perfil === p.id ? "var(--blue)" : "var(--border)",
                    boxShadow: perfil === p.id ? "0 0 0 3px rgba(26,111,196,.10)" : "none",
                  }}
                >
                  <div className="stack-h" style={{ alignItems: "flex-start", gap: 10 }}>
                    <input
                      type="radio"
                      checked={perfil === p.id}
                      onChange={() => setPerfil(p.id)}
                      style={{ width: "auto", marginTop: 3 }}
                    />
                    <div>
                      <div style={{ fontWeight: 600 }}>
                        {p.nome}{" "}
                        <span className="small muted" style={{ fontWeight: 400 }}>
                          — {p.resumo}
                        </span>
                      </div>
                      <div className="small muted" style={{ marginTop: 3 }}>{p.detalhe}</div>
                    </div>
                  </div>
                </label>
              ))}
            </div>
          </div>

          <div className="field">
            <label className="label label-required">Destinos</label>
            {carregandoDest ? (
              <Carregando texto="Carregando destinos…" />
            ) : (
              <SeletorDestinos
                destinos={ativos}
                selecionados={destinos}
                onMudar={(v) => {
                  setTocou(true);
                  setDestinos(v);
                }}
              />
            )}
            <div className="field-help">
              Cadastre e teste destinos em <strong>Destinos</strong>.
            </div>
          </div>

          {perfil === "completo" && (
            <div
              className="card card-tight"
              style={{ background: "var(--red-bg)", borderColor: "#f3b6b6" }}
            >
              <label className="check" style={{ marginBottom: 0 }}>
                <input
                  type="checkbox"
                  checked={aceito}
                  onChange={(e) => setAceito(e.target.checked)}
                />
                <span style={{ color: "#8c1c1c" }}>
                  Entendo que o perfil Completo <strong>PARA o FindFace Multi</strong> em{" "}
                  <strong>{host ? host.name : "este servidor"}</strong> durante a cópia
                  (pode levar horas) e que o reconhecimento facial fica fora do ar nesse
                  período.
                </span>
              </label>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>
            Cancelar
          </button>
          <button
            className="btn btn-primary"
            disabled={enviando || (perfil === "completo" && !aceito)}
          >
            {enviando ? "Disparando…" : "Disparar backup"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ModalDetalhe({ runId, onFechar }) {
  const [run, setRun] = useState(null);
  const [erro, setErro] = useState("");

  useEffect(() => {
    let vivo = true;
    const buscar = () =>
      api
        .backup(runId)
        .then((r) => vivo && setRun(r))
        .catch((e) => vivo && setErro(e.message));

    buscar();
    // Acompanha ao vivo enquanto estiver rodando
    const id = setInterval(() => {
      if (vivo) buscar();
    }, 3000);
    return () => {
      vivo = false;
      clearInterval(id);
    };
  }, [runId]);

  return (
    <div className="modal-bg" onClick={onFechar}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="modal-title">
            Execução #{runId} {run && `— ${run.host_nome} / ${run.profile}`}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onFechar}>Fechar</button>
        </div>
        <div className="modal-body">
          <Erro mensagem={erro} />
          {!run ? (
            <Carregando />
          ) : (
            <div className="stack-v">
              <div className="grid-stats">
                <div className="card card-tight stat">
                  <span className="stat-label">Situação</span>
                  <div><Selo status={run.status} /></div>
                  <span className="stat-sub">{run.stage}</span>
                </div>
                <div className="card card-tight stat">
                  <span className="stat-label">Artefato</span>
                  <div className="mono small">{run.artifact_name || "—"}</div>
                  <span className="stat-sub">{formatBytes(run.size_bytes)}</span>
                </div>
                <div className="card card-tight stat">
                  <span className="stat-label">Checksum SHA-256</span>
                  <div className="mono small" style={{ wordBreak: "break-all" }}>
                    {run.checksum_sha256 ? run.checksum_sha256.slice(0, 32) + "…" : "—"}
                  </div>
                </div>
              </div>

              {run.error && (
                <div className="card card-tight" style={{ background: "var(--red-bg)", borderColor: "#f3b6b6" }}>
                  <div className="small" style={{ color: "#8c1c1c" }}>{run.error}</div>
                </div>
              )}

              <div>
                <div className="section-title">Log da execução</div>
                <div className="log">{run.log || "(sem log ainda)"}</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
