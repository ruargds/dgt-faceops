import React, { useCallback, useEffect, useState } from "react";
import { api, formatData } from "../../api";
import { t } from "../../i18n";
import { usePermissions } from "../../usePermissions";
import {
  fecharSeForaLimpo,
  Carregando,
  ConfirmarDigitando,
  Erro,
  SeletorHost,
  Selo,
  Vazio,
  useHosts,
} from "../Comuns";
import {
  IconAlerta,
  IconAtualizar,
  IconGPU,
  IconLogs,
  IconPlay,
  IconStop,
} from "../Icons";

export default function ServicosView() {
  const { has } = usePermissions();
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();

  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [reiniciando, setReiniciando] = useState(null);
  const [logs, setLogs] = useState(null);
  const [acaoStack, setAcaoStack] = useState(null);
  const [aviso, setAviso] = useState("");

  const carregar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    try {
      setDados(await api.servicos(hostId));
    } catch (ex) {
      setErro(ex.message);
      setDados(null);
    } finally {
      setCarregando(false);
    }
  }, [hostId]);

  useEffect(() => {
    if (hostId) carregar();
  }, [hostId, carregar]);

  async function reiniciar(container) {
    setReiniciando(container);
    setAviso("");
    setErro("");
    try {
      const r = await api.reiniciarContainer(hostId, container);
      setAviso(`${container} reiniciado — estado atual: ${r.estado}.`);
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setReiniciando(null);
    }
  }

  async function verLogs(container) {
    setLogs({ container, texto: "", carregando: true });
    try {
      const r = await api.logsContainer(hostId, container, 400);
      setLogs({ container, texto: r.log, carregando: false });
    } catch (ex) {
      setLogs({ container, texto: `Erro ao buscar o log: ${ex.message}`, carregando: false });
    }
  }

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo={t("Cadastre um servidor primeiro")} />;

  const host = hosts.find((h) => h.id === hostId);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.servicos")}</div>
          <div className="page-sub">
            {t("tela.servicos.sub")}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <button className="btn btn-secondary" onClick={carregar} disabled={carregando}>
            <IconAtualizar size={15} />{t("Atualizar")}</button>
          {has("services.stack") && dados && (
            <>
              <button
                className="btn btn-secondary"
                onClick={() => setAcaoStack("up")}
                title={t("Sobe os containers que estiverem parados")}
              >
                <IconPlay size={15} />{t("Subir stack")}</button>
              <button className="btn btn-danger" onClick={() => setAcaoStack("stop")}>
                <IconStop size={15} />{t("Parar stack")}</button>
            </>
          )}
        </div>
      </div>

      {aviso && (
        <div className="card card-tight" style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 14 }}>
          <span className="small" style={{ color: "var(--green-fg)" }}>{aviso}</span>
        </div>
      )}

      <Erro mensagem={erro} onTentar={carregar} />

      {carregando && !dados && <Carregando texto={t("Consultando o Docker do servidor…")} />}

      {dados && (
        <div className="stack-v">
          <div className="stack-h small muted">{t("Projeto compose")}<span className="mono">{dados.projeto}</span> ·{" "}
            <span className="mono">{dados.compose_file}</span> · {dados.rodando} de{" "}
            {dados.total} rodando
            {dados.jobs > 0 && (
              <span className="pill pill-idle" title={t("Jobs de migração, rodam na subida e saem com 0")}>
                +{dados.jobs} job(s)
              </span>
            )}
            {dados.com_problema > 0 && (
              <span className="pill pill-warn">
                <IconAlerta size={12} /> {dados.com_problema} com problema
              </span>
            )}
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("Serviço")}</th>
                  <th>{t("Estado")}</th>
                  <th>{t("Saúde")}</th>
                  <th className="right">{t("Reinícios")}</th>
                  <th>{t("Desde")}</th>
                  <th style={{ width: 1 }}></th>
                </tr>
              </thead>
              <tbody>
                {dados.servicos.map((s) => (
                  <tr key={s.nome}>
                    <td>
                      <div className="stack-h" style={{ gap: 6 }}>
                        <span className="mono">{s.servico}</span>
                        {s.usa_gpu && (
                          <span className="pill pill-info" title={t("Usa GPU")}>
                            <IconGPU size={11} />
                          </span>
                        )}
                        {s.guarda_dados && (
                          <span className="pill pill-idle" title={t("Guarda dados em disco")}>
                            dados
                          </span>
                        )}
                        {s.e_job && (
                          <span
                            className="pill pill-idle"
                            title="Job de execução única: roda na subida e sai. Sair com 0 é o esperado."
                          >
                            job
                          </span>
                        )}
                      </div>
                      <div className="small muted mono">{s.nome}</div>
                    </td>
                    <td>
                      {s.e_job && s.exit_code === 0 ? (
                        <span className="pill pill-ok" title={t("Job concluído com sucesso")}>{t("Concluído")}</span>
                      ) : (
                        <Selo status={s.estado} />
                      )}
                      {s.e_job && s.exit_code !== 0 && (
                        <div className="small" style={{ color: "var(--red)", marginTop: 3 }}>
                          job falhou (exit {s.exit_code})
                        </div>
                      )}
                      {s.oom_killed && (
                        <div className="small" style={{ color: "var(--red)", marginTop: 3 }}>{t("morto por falta de memória")}</div>
                      )}
                    </td>
                    <td>{s.saude ? <Selo status={s.saude} /> : <span className="muted small">—</span>}</td>
                    <td className="right mono">
                      <span
                        style={{
                          color: s.reinicios > 3 ? "var(--red)" : s.reinicios > 0 ? "var(--amber)" : "inherit",
                        }}
                      >
                        {s.reinicios}
                      </span>
                    </td>
                    <td className="small muted">{formatData(s.iniciado_em)}</td>
                    <td>
                      <div className="stack-h" style={{ gap: 6, flexWrap: "nowrap" }}>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => verLogs(s.nome)}
                          title={t("Ver últimas linhas do log")}
                        >
                          <IconLogs size={14} />
                        </button>
                        {has("services.restart") && !s.e_job && (
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => reiniciar(s.nome)}
                            disabled={reiniciando === s.nome}
                          >
                            {reiniciando === s.nome ? "…" : "Reiniciar"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {logs && (
        <div className="modal-bg" {...fecharSeForaLimpo(() => setLogs(null))}>
          <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal-head">
              <div className="modal-title mono">{logs.container}</div>
              <button className="btn btn-ghost btn-sm" onClick={() => setLogs(null)}>{t("Fechar")}</button>
            </div>
            <div className="modal-body">
              {logs.carregando ? (
                <Carregando texto={t("Buscando o log…")} />
              ) : (
                <div className="log">{logs.texto || "(log vazio)"}</div>
              )}
            </div>
          </div>
        </div>
      )}

      {acaoStack && host && (
        <ConfirmarDigitando
          titulo={acaoStack === "stop" ? "Parar o stack do FindFace Multi" : "Subir o stack"}
          palavra={host.name}
          rotuloBotao={acaoStack === "stop" ? "Parar tudo" : "Subir tudo"}
          aviso={
            acaoStack === "stop"
              ? `Isto PARA todos os containers do FindFace Multi em ${host.name}. O reconhecimento facial fica fora do ar até o stack subir de novo, e os eventos do período não são gravados.`
              : `Isto sobe todos os containers do FindFace Multi em ${host.name}. Se o stack já estiver de pé, nada muda.`
          }
          onConfirmar={async (confirmacao) => {
            await api.acaoStack(hostId, acaoStack, confirmacao);
            setAviso(`Stack: '${acaoStack}' executado em ${host.name}.`);
            await carregar();
          }}
          onFechar={() => setAcaoStack(null)}
        />
      )}
    </>
  );
}
