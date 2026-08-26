import React, { useCallback, useEffect, useState } from "react";
import { api, formatData } from "../../api";
import { t } from "../../i18n";
import { usePermissions } from "../../usePermissions";
import {
  fecharSeForaLimpo,
  Carregando,
  Erro,
  SeletorDestinos,
  Vazio,
  useDestinos,
  useHosts,
} from "../Comuns";
import { IconAgenda, IconAtualizar, IconLixeira, IconPlay } from "../Icons";
import { PERFIS } from "./BackupsView";

/**
 * Atalhos de recorrência. A maioria das equipes nunca vai escrever cron à
 * mão — mas quem sabe escrever não pode ficar preso a uma lista fechada,
 * então o campo continua editável.
 */
const ATALHOS = [
  { rotulo: "Todo dia às 02:00", cron: "0 2 * * *" },
  { rotulo: "Todo dia às 03:30", cron: "30 3 * * *" },
  { rotulo: "A cada 6 horas", cron: "0 */6 * * *" },
  { rotulo: "Segunda a sexta, 01:00", cron: "0 1 * * 1-5" },
  { rotulo: "Todo domingo às 04:00", cron: "0 4 * * 0" },
  { rotulo: "Todo dia 1º às 03:00", cron: "0 3 1 * *" },
];

export default function AgendamentosView() {
  const { has } = usePermissions();
  const { hosts, carregando: carregandoHosts } = useHosts(false);
  const { ativos: destinosAtivos, nomePorId } = useDestinos();

  const [lista, setLista] = useState([]);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [editando, setEditando] = useState(null);
  const [aviso, setAviso] = useState("");

  const carregar = useCallback(async () => {
    setErro("");
    try {
      setLista(await api.agendamentos());
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function alternar(a) {
    try {
      await api.atualizarAgendamento(a.id, { enabled: !a.enabled });
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    }
  }

  async function executar(a) {
    setAviso("");
    try {
      await api.executarAgendamento(a.id);
      setAviso(`'${a.name}' disparado. Acompanhe na tela de Backups.`);
    } catch (ex) {
      setErro(ex.message);
    }
  }

  async function remover(a) {
    if (!window.confirm(`Remover o agendamento '${a.name}'?`)) return;
    try {
      await api.removerAgendamento(a.id);
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    }
  }

  if (carregandoHosts || carregando) return <Carregando />;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.agendamentos")}</div>
          <div className="page-sub">
            {t("tela.agendamentos.sub")}
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={carregar}>
            <IconAtualizar size={15} />{t("Atualizar")}</button>
          {has("schedules.manage") && hosts.length > 0 && (
            <button className="btn btn-primary" onClick={() => setEditando({})}>
              <IconAgenda size={15} />{t("Novo agendamento")}</button>
          )}
        </div>
      </div>

      {aviso && (
        <div className="card card-tight" style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 14 }}>
          <span className="small" style={{ color: "var(--green-fg)" }}>{aviso}</span>
        </div>
      )}

      <Erro mensagem={erro} onTentar={carregar} />

      {lista.length === 0 ? (
        <Vazio titulo={t("Nenhum agendamento")}>
          Sugestão para começar: <strong>{t("Essencial")}</strong> todo dia às 02:00 em cada
          servidor, e <strong>{t("Config")}</strong>{t("a cada 6 horas. O")}<strong>{t("Completo")}</strong>{" "}
          só mensal, em janela combinada.
        </Vazio>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("Nome")}</th>
                <th>{t("Servidor")}</th>
                <th>{t("Perfil")}</th>
                <th>{t("Recorrência")}</th>
                <th>{t("Próxima")}</th>
                <th>{t("Última")}</th>
                <th>{t("Retenção")}</th>
                <th style={{ width: 1 }}></th>
              </tr>
            </thead>
            <tbody>
              {lista.map((a) => (
                <tr key={a.id} style={{ opacity: a.enabled ? 1 : 0.55 }}>
                  <td>
                    <div style={{ fontWeight: 600 }}>{a.name}</div>
                    <div className="stack-h" style={{ gap: 4, marginTop: 3 }}>
                      {(a.destinations || []).map((d) => (
                        <span className="pill pill-idle" key={d}>
                          {nomePorId[d] || `#${d}`}
                        </span>
                      ))}
                      {(a.destinations || []).length === 0 && (
                        <span
                          className="pill pill-idle"
                          title={t("Usa os destinos marcados como padrao")}
                        >{t("padrão")}</span>
                      )}
                    </div>
                  </td>
                  <td className="mono small">{a.host_nome}</td>
                  <td>
                    <span className="pill pill-idle">{a.profile}</span>
                    {a.profile === "completo" && (
                      <div className="small" style={{ color: "var(--amber)", marginTop: 3 }}>{t("com parada")}</div>
                    )}
                  </td>
                  <td>
                    <div className="small">{a.cron_legivel}</div>
                    <div className="small muted mono">{a.cron}</div>
                  </td>
                  <td className="small">
                    {a.enabled ? formatData(a.next_run_at) : <span className="muted">pausado</span>}
                  </td>
                  <td className="small">
                    {a.last_run_at ? (
                      <>
                        {formatData(a.last_run_at)}
                        <div
                          className="small"
                          style={{
                            color: a.last_status === "sucesso" ? "var(--green)" : "var(--red)",
                          }}
                        >
                          {a.last_status}
                        </div>
                      </>
                    ) : (
                      <span className="muted">nunca</span>
                    )}
                  </td>
                  <td className="small mono">{a.retention_days}d</td>
                  <td>
                    <div className="stack-h" style={{ gap: 6, flexWrap: "nowrap" }}>
                      {has("backups.run") && (
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => executar(a)}
                          title={t("Executar agora")}
                        >
                          <IconPlay size={13} />
                        </button>
                      )}
                      {has("schedules.manage") && (
                        <>
                          <button className="btn btn-secondary btn-sm" onClick={() => alternar(a)}>
                            {a.enabled ? "Pausar" : "Ativar"}
                          </button>
                          <button className="btn btn-secondary btn-sm" onClick={() => setEditando(a)}>{t("Editar")}</button>
                          <button className="btn btn-danger btn-sm" onClick={() => remover(a)}>
                            <IconLixeira size={13} />
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editando && (
        <ModalAgendamento
          inicial={editando}
          hosts={hosts.filter((h) => h.enabled)}
          destinosDisponiveis={destinosAtivos}
          onFechar={() => setEditando(null)}
          onPronto={async () => {
            setEditando(null);
            await carregar();
          }}
        />
      )}
    </>
  );
}

function ModalAgendamento({ inicial, hosts, destinosDisponiveis, onFechar, onPronto }) {
  const editando = Boolean(inicial.id);
  const [nome, setNome] = useState(inicial.name || "");
  const [hostId, setHostId] = useState(inicial.host_id || (hosts[0] && hosts[0].id));
  const [perfil, setPerfil] = useState(inicial.profile || "essencial");
  const [cron, setCron] = useState(inicial.cron || "0 2 * * *");
  const [destinos, setDestinos] = useState(inicial.destinations || []);
  const [retencao, setRetencao] = useState(inicial.retention_days ?? 30);
  const [aceito, setAceito] = useState(inicial.allow_downtime || false);
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  const host = hosts.find((h) => h.id === hostId);

  async function enviar(e) {
    e.preventDefault();
    setErro("");
    setEnviando(true);
    const corpo = {
      name: nome,
      host_id: hostId,
      perfil,
      cron,
      destinos,
      retencao_dias: Number(retencao),
      allow_downtime: aceito,
      enabled: inicial.enabled !== undefined ? inicial.enabled : true,
    };
    try {
      if (editando) {
        await api.atualizarAgendamento(inicial.id, corpo);
      } else {
        await api.criarAgendamento(corpo);
      }
      await onPronto();
    } catch (ex) {
      setErro(ex.message);
      setEnviando(false);
    }
  }

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={enviar}>
        <div className="modal-head">
          <div className="modal-title">
            {editando ? "Editar agendamento" : "Novo agendamento"}
          </div>
        </div>
        <div className="modal-body">
          {erro && <div className="login-err">{erro}</div>}

          <div className="row row-2">
            <div className="field">
              <label className="label label-required">{t("Nome")}</label>
              <input
                value={nome}
                onChange={(e) => setNome(e.target.value)}
                placeholder={t("Essencial diário — appserver")}
                required
              />
            </div>
            <div className="field">
              <label className="label label-required">{t("Servidor")}</label>
              <select
                value={hostId ?? ""}
                onChange={(e) => setHostId(Number(e.target.value))}
                disabled={editando}
              >
                {hosts.map((h) => (
                  <option key={h.id} value={h.id}>{h.name}</option>
                ))}
              </select>
              {editando && (
                <div className="field-help">
                  O servidor não muda depois de criado — crie outro agendamento.
                </div>
              )}
            </div>
          </div>

          <div className="row row-2">
            <div className="field">
              <label className="label label-required">{t("Perfil")}</label>
              <select value={perfil} onChange={(e) => setPerfil(e.target.value)}>
                {PERFIS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.nome} — {p.resumo}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label label-required">Retenção (dias)</label>
              <input
                type="number"
                min={0}
                max={3650}
                value={retencao}
                onChange={(e) => setRetencao(e.target.value)}
                required
              />
              <div className="field-help">
                Artefatos locais mais velhos que isso são apagados. 0 = nunca apagar.
              </div>
            </div>
          </div>

          <div className="field">
            <label className="label label-required">{t("Recorrência")}</label>
            <div className="stack-h" style={{ gap: 6, marginBottom: 8 }}>
              {ATALHOS.map((a) => (
                <button
                  type="button"
                  key={a.cron}
                  className={`btn btn-sm ${cron === a.cron ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setCron(a.cron)}
                >
                  {a.rotulo}
                </button>
              ))}
            </div>
            <input
              className="mono"
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              placeholder="0 2 * * *"
              required
            />
            <div className="field-help">
              Formato cron de 5 campos: minuto hora dia mês dia-da-semana. Fuso do painel
              (America/Sao_Paulo).
            </div>
          </div>

          <div className="field">
            <label className="label">{t("Destinos")}</label>
            <SeletorDestinos
              destinos={destinosDisponiveis || []}
              selecionados={destinos}
              onMudar={setDestinos}
            />
            <div className="field-help">
              Sem nenhum marcado, o agendamento usa os destinos marcados como
              padrão na hora de rodar — assim ele continua valendo se o destino
              padrão mudar depois.
            </div>
          </div>

          {perfil === "completo" && (
            <div
              className="card card-tight"
              style={{ background: "var(--red-bg)", borderColor: "var(--red-bd)" }}
            >
              <label className="check" style={{ marginBottom: 0 }}>
                <input
                  type="checkbox"
                  checked={aceito}
                  onChange={(e) => setAceito(e.target.checked)}
                />
                <span style={{ color: "var(--red-fg)" }}>
                  Autorizo janela de manutenção recorrente: este agendamento{" "}
                  <strong>{t("PARA o FindFace Multi")}</strong> em{" "}
                  <strong>{host ? host.name : "este servidor"}</strong> toda vez que rodar.
                  Sem este aceite o agendamento é bloqueado na hora de executar.
                </span>
              </label>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>{t("Cancelar")}</button>
          <button className="btn btn-primary" disabled={enviando || (perfil === "completo" && !aceito)}>
            {enviando ? "Salvando…" : editando ? "Salvar" : "Criar agendamento"}
          </button>
        </div>
      </form>
    </div>
  );
}
