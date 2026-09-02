import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, formatData, formatDuracao } from "../../api";
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
  IconAuditoria,
} from "../Icons";

/**
 * Histórico de quedas de UM serviço.
 *
 * Sai da tabela de `incidentes`, que o ciclo do monitor já preenche a
 * cada passada. Isso é o que torna esta aba barata: ela não abre SSH,
 * não consulta o servidor e não cria tabela nem retenção nova — a
 * retenção de incidentes (padrão 30 dias) já recicla. Os itens chegam
 * junto com a lista de serviços, então abrir aqui é instantâneo e não
 * faz uma requisição nova.
 *
 * O que NÃO está aqui, e por quê: histórico de start/stop manual. Isso é
 * auditoria, e auditoria tem tela própria, com busca e filtro — repetir
 * aqui seria um segundo lugar contando a mesma coisa, que divergiria.
 * O rodapé aponta para lá.
 */
function HistoricoServico({ servico, dias, itens, onFechar }) {
  const fechadas = itens.filter((i) => !i.aberto);
  const totalFora = itens.reduce((acc, i) => acc + (i.duracao_s || 0), 0);
  const aberta = itens.find((i) => i.aberto);

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="modal-title mono">{servico.servico}</div>
            <div className="small muted">
              {t("Quedas nos últimos")} {dias} {t("dias")} · {t("do histórico do monitor")}
            </div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onFechar}>{t("Fechar")}</button>
        </div>
        <div className="modal-body">
          {itens.length === 0 ? (
            <Vazio titulo={t("Nenhuma queda registrada")}>
              <div className="small muted" style={{ marginTop: 6 }}>
                {t("Este serviço não saiu do ar na janela — ou o monitor ainda não o observou cair.")}
              </div>
            </Vazio>
          ) : (
            <>
              <div className="stack-h" style={{ gap: 18, marginBottom: 14, flexWrap: "wrap" }}>
                <div>
                  <div className="small muted">{t("Quedas")}</div>
                  <strong style={{ fontSize: 18 }}>{itens.length}</strong>
                </div>
                <div>
                  <div className="small muted">{t("Tempo fora somado")}</div>
                  <strong style={{ fontSize: 18 }}>{formatDuracao(totalFora)}</strong>
                </div>
                <div>
                  <div className="small muted">{t("Já normalizadas")}</div>
                  <strong style={{ fontSize: 18 }}>{fechadas.length}</strong>
                </div>
              </div>

              {aberta && (
                <div
                  className="card card-tight"
                  style={{ background: "var(--red-bg)", borderColor: "var(--red-bd)", marginBottom: 14 }}
                >
                  <span className="small" style={{ color: "var(--red-fg)" }}>
                    <IconAlerta size={13} /> {t("Está fora agora, desde")}{" "}
                    {formatData(aberta.inicio)}
                    {aberta.duracao_s ? ` (${formatDuracao(aberta.duracao_s)})` : ""}
                  </span>
                </div>
              )}

              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("Caiu")}</th>
                      <th>{t("Voltou")}</th>
                      <th className="right">{t("Ficou fora")}</th>
                      <th>{t("O que foi")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {itens.map((i) => (
                      <tr key={i.id}>
                        <td className="small">{formatData(i.inicio)}</td>
                        <td className="small">
                          {i.fim ? (
                            formatData(i.fim)
                          ) : (
                            <span className="pill pill-err">{t("ainda fora")}</span>
                          )}
                        </td>
                        <td className="right mono small">{formatDuracao(i.duracao_s)}</td>
                        <td className="small">
                          {i.texto}
                          {i.causa_provavel && (
                            <div className="muted" style={{ marginTop: 2 }}>
                              {i.causa_provavel}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="small muted" style={{ marginTop: 12 }}>
            {t("Quem parou, subiu ou reiniciou este serviço pelo painel fica em Auditoria, com busca e filtro. Aqui só o que o monitor observou.")}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ServicosView({ alvo }) {
  const { has } = usePermissions();
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();

  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [reiniciando, setReiniciando] = useState(null);
  const [logs, setLogs] = useState(null);
  const [acaoStack, setAcaoStack] = useState(null);
  const [aviso, setAviso] = useState("");
  // Histórico de indisponibilidade do host, para a coluna e a aba de
  // histórico por serviço. Vem da tabela de incidentes, que o ciclo do
  // monitor já preenche: nenhum SSH, nenhuma tabela nova e nenhuma
  // retenção nova — `incidentes.retencao_dias` (padrão 30) já limpa.
  const [historico, setHistorico] = useState([]);
  const [verHistorico, setVerHistorico] = useState(null);
  const [parando, setParando] = useState(null);
  const [confirmarParada, setConfirmarParada] = useState(null);

  const JANELA_DIAS = 7;

  // Chegou de um atalho de alerta ("ir para Serviços") — abre já no host
  // certo, em vez do primeiro host ativo que useHosts escolheria sozinho.
  useEffect(() => {
    if (alvo && alvo.hostId) setHostId(alvo.hostId);
  }, [alvo, setHostId]);

  const carregar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    try {
      // Duas chamadas em paralelo: os serviços (que abrem SSH) e o
      // histórico (consulta local). O histórico não atrasa a tela.
      const [servicos, inc] = await Promise.all([
        api.servicos(hostId),
        api.incidentesRecentes(JANELA_DIAS, hostId).catch(() => ({ incidentes: [] })),
      ]);
      setDados(servicos);
      setHistorico(inc.incidentes || []);
    } catch (ex) {
      setErro(ex.message);
      setDados(null);
    } finally {
      setCarregando(false);
    }
  }, [hostId]);

  // Resumo por serviço, calculado uma vez. Sem isto, cada linha da
  // tabela varreria o histórico inteiro a cada render.
  const resumoPorServico = useMemo(() => {
    const mapa = new Map();
    for (const i of historico) {
      if (i.tipo !== "servico" || !i.servico) continue;
      const atual = mapa.get(i.servico) || { quedas: 0, fora_s: 0, aberto: false, ultima: null };
      atual.quedas += 1;
      atual.fora_s += i.duracao_s || 0;
      atual.aberto = atual.aberto || i.aberto;
      if (!atual.ultima || i.inicio > atual.ultima) atual.ultima = i.inicio;
      mapa.set(i.servico, atual);
    }
    return mapa;
  }, [historico]);

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

  async function power(container, acao, confirmar = "") {
    setParando(container);
    setAviso("");
    setErro("");
    try {
      const r = await api.powerContainer(hostId, container, acao, confirmar);
      setAviso(
        acao === "stop"
          ? `${container} parado — estado atual: ${r.estado}. O monitor vai registrar isto como queda.`
          : `${container} iniciado — estado atual: ${r.estado}.`,
      );
      await carregar();
    } catch (ex) {
      setErro(ex.message);
      throw ex;
    } finally {
      setParando(null);
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
            <IconAtualizar size={15} /> {t("Atualizar")}</button>
          {has("services.stack") && dados && (
            <>
              <button
                className="btn btn-secondary"
                onClick={() => setAcaoStack("up")}
                title={t("Sobe os containers que estiverem parados")}
              >
                <IconPlay size={15} /> {t("Subir stack")}</button>
              <button className="btn btn-danger" onClick={() => setAcaoStack("stop")}>
                <IconStop size={15} /> {t("Parar stack")}</button>
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
          <div className="stack-h small muted">{t("Projeto compose")} <span className="mono">{dados.projeto}</span> ·{" "}
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
                  <th title={t("Quedas registradas nos últimos 7 dias")}>
                    {t("Histórico (7d)")}
                  </th>
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
                    <td className="small">
                      {(() => {
                        const h = resumoPorServico.get(s.servico);
                        if (!h) {
                          return (
                            <span className="muted" title={t("Nenhuma queda registrada na janela")}>
                              {t("estável")}
                            </span>
                          );
                        }
                        return (
                          <button
                            type="button"
                            className="link-inline"
                            onClick={() => setVerHistorico(s)}
                            title={t("Ver as quedas deste serviço")}
                          >
                            <strong style={{ color: h.quedas > 3 ? "var(--red)" : "var(--amber)" }}>
                              {h.quedas}×
                            </strong>
                            {h.fora_s > 0 && (
                              <span className="muted"> · {formatDuracao(h.fora_s)} {t("fora")}</span>
                            )}
                          </button>
                        );
                      })()}
                    </td>
                    <td>
                      <div className="stack-h" style={{ gap: 6, flexWrap: "nowrap" }}>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => verLogs(s.nome)}
                          title={t("Ver últimas linhas do log")}
                        >
                          <IconLogs size={14} />
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => setVerHistorico(s)}
                          title={t("Histórico de quedas deste serviço")}
                        >
                          <IconAuditoria size={14} />
                        </button>
                        {/* Parar e subir. Job não entra: ele roda na
                            subida e sai — "parar" um job concluído não
                            significa nada. */}
                        {has("services.power") && !s.e_job && (
                          s.estado === "running" ? (
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => setConfirmarParada(s)}
                              disabled={parando === s.nome}
                              title={t("Parar este serviço")}
                            >
                              <IconStop size={13} />
                            </button>
                          ) : (
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => power(s.nome, "start").catch(() => {})}
                              disabled={parando === s.nome}
                              title={t("Subir este serviço")}
                            >
                              {parando === s.nome ? "…" : <IconPlay size={13} />}
                            </button>
                          )
                        )}
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

      {verHistorico && (
        <HistoricoServico
          servico={verHistorico}
          dias={JANELA_DIAS}
          itens={historico.filter(
            (i) => i.tipo === "servico" && i.servico === verHistorico.servico,
          )}
          onFechar={() => setVerHistorico(null)}
        />
      )}

      {confirmarParada && (
        <ConfirmarDigitando
          titulo={`${t("Parar")} ${confirmarParada.servico}`}
          aviso={
            `Isto PARA o container ${confirmarParada.nome} e ele FICA parado — ` +
            "reiniciar volta sozinho, parar não. " +
            "O monitor vai registrar como queda e, se houver regra de aviso, " +
            "mandar no Telegram. Para subir de novo, use o botão de play na " +
            "mesma linha."
          }
          palavra={confirmarParada.nome}
          rotuloBotao={t("Parar serviço")}
          onConfirmar={(texto) => power(confirmarParada.nome, "stop", texto)}
          onFechar={() => setConfirmarParada(null)}
        />
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
