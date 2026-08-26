import React, { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatData } from "../../api";
import { t } from "../../i18n";
import { usePermissions } from "../../usePermissions";
import {
  fecharSeForaLimpo, Carregando, Erro, Vazio,
} from "../Comuns";
import { IconAlerta, IconChave, IconLixeira, IconMais, IconOk } from "../Icons";

const TIPOS = [
  {
    id: "local",
    nome: "Disco do painel",
    resumo: "Pasta na máquina onde o painel roda",
    detalhe:
      "Rápido de restaurar. Não protege contra perda do site — use junto com um destino externo.",
  },
  {
    id: "azure",
    nome: "Azure Blob Storage",
    resumo: "Container no Azure, tier Cool por padrão",
    detalhe:
      "Barato para arquivo grande. Se o painel roda no Azure, o upload não sai da rede do provedor.",
  },
  {
    id: "rclone",
    nome: "rclone (qualquer provedor)",
    resumo: "Google Drive, S3, B2, OneDrive, SFTP, WebDAV, Dropbox…",
    detalhe:
      "Cole o bloco de configuração gerado por `rclone config`. Um tipo cobre dezenas de provedores.",
  },
];

export default function DestinosView() {
  const { has } = usePermissions();
  const [lista, setLista] = useState([]);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [editando, setEditando] = useState(null);
  const [testes, setTestes] = useState({});

  const carregar = useCallback(async () => {
    setErro("");
    try {
      setLista(await api.destinos());
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function testar(d) {
    setTestes((t) => ({ ...t, [d.id]: { carregando: true } }));
    try {
      const r = await api.testarDestino(d.id);
      setTestes((t) => ({ ...t, [d.id]: { ...r, carregando: false } }));
      await carregar();
    } catch (ex) {
      setTestes((t) => ({
        ...t,
        [d.id]: { ok: false, detalhe: ex.message, carregando: false },
      }));
    }
  }

  async function remover(d) {
    if (!window.confirm(`Remover o destino '${d.nome}'?`)) return;
    try {
      await api.removerDestino(d.id);
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    }
  }

  if (carregando) return <Carregando />;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.destinos")}</div>
          <div className="page-sub">
            {t("tela.destinos.sub")}
          </div>
        </div>
        {has("destinations.manage") && (
          <div className="page-actions">
            <button className="btn btn-primary" onClick={() => setEditando({ tipo: "local" })}>
              <IconMais size={15} /> Novo destino
            </button>
          </div>
        )}
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {lista.length === 0 ? (
        <Vazio titulo="Nenhum destino cadastrado">
          Sem destino, o backup roda no servidor e não tem para onde ir.
        </Vazio>
      ) : (
        <div className="grid-cards">
          {lista.map((d) => {
            const teste = testes[d.id];
            const tipo = TIPOS.find((t) => t.id === d.tipo);
            return (
              <div className="card" key={d.id} style={{ opacity: d.enabled ? 1 : 0.6 }}>
                <div
                  className="stack-h"
                  style={{ justifyContent: "space-between", marginBottom: 4 }}
                >
                  <strong style={{ fontSize: 15, color: "var(--navy)" }}>{d.nome}</strong>
                  <div className="stack-h" style={{ gap: 5 }}>
                    {d.padrao && <span className="pill pill-info">padrão</span>}
                    {!d.enabled && <span className="pill pill-idle">desativado</span>}
                  </div>
                </div>

                <div className="small muted">{d.descricao || (tipo && tipo.resumo)}</div>

                <div className="small mono" style={{ margin: "10px 0 6px", wordBreak: "break-all" }}>
                  {d.tipo === "local" && d.caminho}
                  {d.tipo === "azure" && `azure://${d.azure_container} (${d.azure_tier})`}
                  {d.tipo === "rclone" && `${d.rclone_remote}:${d.rclone_caminho}`}
                </div>

                {d.tem_credencial && (
                  <div className="stack-h small muted" style={{ gap: 6, marginBottom: 8 }}>
                    <IconChave size={13} />
                    credencial guardada · <span className="mono">{d.cred_fingerprint}</span>
                  </div>
                )}

                <div className="small muted" style={{ marginBottom: 10 }}>
                  Retenção:{" "}
                  {d.retencao_dias > 0
                    ? `${d.retencao_dias} dias`
                    : "sem limpeza automática"}
                </div>

                <div
                  className="card card-tight"
                  style={{
                    marginBottom: 12,
                    background: d.last_test_ok ? "var(--green-bg)" : "var(--bg-2)",
                    borderColor: d.last_test_ok ? "var(--green-bd)" : "var(--border)",
                  }}
                >
                  {teste && !teste.carregando ? (
                    <div
                      className="small"
                      style={{ color: teste.ok ? "var(--green-fg)" : "var(--red-fg)" }}
                    >
                      {teste.ok ? <IconOk size={13} /> : <IconAlerta size={13} />}{" "}
                      {teste.detalhe}
                      {teste.livre_bytes !== undefined && (
                        <div style={{ marginTop: 3 }}>
                          {formatBytes(teste.livre_bytes)} livres de{" "}
                          {formatBytes(teste.total_bytes)}
                        </div>
                      )}
                      {teste.sobre && (
                        <div className="mono" style={{ marginTop: 4, fontSize: 11 }}>
                          {teste.sobre}
                        </div>
                      )}
                    </div>
                  ) : d.last_test_at ? (
                    <div className="small" style={{ color: d.last_test_ok ? "var(--green-fg)" : "var(--red-fg)" }}>
                      {d.last_test_ok ? "Último teste OK" : d.last_test_error}
                      <div className="muted" style={{ marginTop: 2 }}>
                        {formatData(d.last_test_at)}
                      </div>
                    </div>
                  ) : (
                    <div className="small muted">Nunca testado</div>
                  )}
                </div>

                <div className="stack-h" style={{ gap: 6 }}>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => testar(d)}
                    disabled={teste && teste.carregando}
                  >
                    {teste && teste.carregando ? "Testando…" : "Testar"}
                  </button>
                  {has("destinations.manage") && (
                    <>
                      <button className="btn btn-secondary btn-sm" onClick={() => setEditando(d)}>
                        Editar
                      </button>
                      <button className="btn btn-danger btn-sm" onClick={() => remover(d)}>
                        <IconLixeira size={13} />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {editando && (
        <ModalDestino
          inicial={editando}
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

function ModalDestino({ inicial, onFechar, onPronto }) {
  const editando = Boolean(inicial.id);
  const [f, setF] = useState({
    nome: inicial.nome || "",
    descricao: inicial.descricao || "",
    tipo: inicial.tipo || "local",
    enabled: inicial.enabled !== undefined ? inicial.enabled : true,
    padrao: inicial.padrao || false,
    retencao_dias: inicial.retencao_dias ?? 0,
    caminho: inicial.caminho || "/data/backups",
    azure_container: inicial.azure_container || "faceops-backups",
    azure_tier: inicial.azure_tier || "Cool",
    rclone_remote: inicial.rclone_remote || "",
    rclone_caminho: inicial.rclone_caminho || "FaceOps/backups",
    rclone_flags: inicial.rclone_flags || "",
  });
  const [segredos, setSegredos] = useState({ azure_conn: "", rclone_conf: "" });
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  const set = (campo) => (e) =>
    setF((a) => ({
      ...a,
      [campo]: e.target.type === "checkbox" ? e.target.checked : e.target.value,
    }));

  async function enviar(e) {
    e.preventDefault();
    setErro("");
    setEnviando(true);
    const corpo = { ...f, retencao_dias: Number(f.retencao_dias) };
    // Só envia segredo digitado — vazio apagaria o que está no cofre
    if (segredos.azure_conn) corpo.azure_conn = segredos.azure_conn;
    if (segredos.rclone_conf) corpo.rclone_conf = segredos.rclone_conf;
    try {
      if (editando) await api.atualizarDestino(inicial.id, corpo);
      else await api.criarDestino(corpo);
      await onPronto();
    } catch (ex) {
      setErro(ex.message);
      setEnviando(false);
    }
  }

  const tipoInfo = TIPOS.find((t) => t.id === f.tipo);

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <form className="modal modal-wide" onClick={(e) => e.stopPropagation()} onSubmit={enviar}>
        <div className="modal-head">
          <div className="modal-title">
            {editando ? `Editar ${inicial.nome}` : "Novo destino"}
          </div>
        </div>

        <div className="modal-body">
          {erro && <div className="login-err">{erro}</div>}

          {!editando && (
            <div className="field">
              <label className="label label-required">Tipo</label>
              <div className="stack-v" style={{ gap: 8 }}>
                {TIPOS.map((t) => (
                  <label
                    key={t.id}
                    className="card card-tight"
                    style={{
                      cursor: "pointer",
                      borderColor: f.tipo === t.id ? "var(--blue)" : "var(--border)",
                      boxShadow: f.tipo === t.id ? "0 0 0 3px rgba(26,111,196,.10)" : "none",
                    }}
                  >
                    <div className="stack-h" style={{ alignItems: "flex-start", gap: 10 }}>
                      <input
                        type="radio"
                        checked={f.tipo === t.id}
                        onChange={() => setF((a) => ({ ...a, tipo: t.id }))}
                        style={{ width: "auto", marginTop: 3 }}
                      />
                      <div>
                        <div style={{ fontWeight: 600 }}>
                          {t.nome}{" "}
                          <span className="small muted" style={{ fontWeight: 400 }}>
                            — {t.resumo}
                          </span>
                        </div>
                        <div className="small muted" style={{ marginTop: 3 }}>
                          {t.detalhe}
                        </div>
                      </div>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}

          <div className="row row-2">
            <div className="field">
              <label className="label label-required">Nome</label>
              <input value={f.nome} onChange={set("nome")} required placeholder="Azure — cofre frio" />
            </div>
            <div className="field">
              <label className="label">Descrição</label>
              <input value={f.descricao} onChange={set("descricao")} />
            </div>
          </div>

          {f.tipo === "local" && (
            <div className="field">
              <label className="label label-required">Caminho</label>
              <input className="mono" value={f.caminho} onChange={set("caminho")} required />
              <div className="field-help">
                Caminho <strong>dentro do container</strong> do painel. O padrão{" "}
                <span className="mono">/data/backups</span> já está mapeado para o disco
                do host no docker-compose.
              </div>
            </div>
          )}

          {f.tipo === "azure" && (
            <>
              <div className="row row-2">
                <div className="field">
                  <label className="label label-required">Container</label>
                  <input className="mono" value={f.azure_container} onChange={set("azure_container")} />
                </div>
                <div className="field">
                  <label className="label">Camada de armazenamento</label>
                  <select value={f.azure_tier} onChange={set("azure_tier")}>
                    <option value="Hot">Hot — acesso frequente, mais caro</option>
                    <option value="Cool">Cool — recomendado para backup</option>
                    <option value="Archive">Archive — barato, restauro leva horas</option>
                  </select>
                </div>
              </div>
              <div className="field">
                <label className={`label ${editando ? "" : "label-required"}`}>
                  Connection string
                </label>
                <textarea
                  rows={3}
                  value={segredos.azure_conn}
                  onChange={(e) => setSegredos((a) => ({ ...a, azure_conn: e.target.value }))}
                  placeholder={
                    editando
                      ? "Deixe em branco para manter a atual"
                      : "DefaultEndpointsProtocol=https;AccountName=…;AccountKey=…"
                  }
                  required={!editando}
                />
                <div className="field-help">
                  Portal do Azure → Conta de armazenamento → Chaves de acesso. Guardada
                  cifrada (Fernet); nunca é devolvida pela API depois de salva.
                </div>
              </div>
            </>
          )}

          {f.tipo === "rclone" && (
            <>
              <div className="row row-2">
                <div className="field">
                  <label className="label label-required">Nome do remote</label>
                  <input
                    className="mono"
                    value={f.rclone_remote}
                    onChange={set("rclone_remote")}
                    placeholder="gdrive"
                    required
                  />
                  <div className="field-help">
                    O que está entre colchetes no bloco abaixo.
                  </div>
                </div>
                <div className="field">
                  <label className="label">Caminho no destino</label>
                  <input className="mono" value={f.rclone_caminho} onChange={set("rclone_caminho")} />
                </div>
              </div>

              <div className="field">
                <label className={`label ${editando ? "" : "label-required"}`}>
                  Bloco de configuração do rclone
                </label>
                <textarea
                  rows={8}
                  value={segredos.rclone_conf}
                  onChange={(e) => setSegredos((a) => ({ ...a, rclone_conf: e.target.value }))}
                  placeholder={
                    editando
                      ? "Deixe em branco para manter a atual"
                      : "[gdrive]\ntype = drive\nscope = drive\ntoken = {\"access_token\":\"…\"}"
                  }
                  required={!editando}
                />
                <div className="field-help">
                  Gere com <span className="mono">rclone config</span> em qualquer máquina
                  e cole aqui a seção do remote, <strong>incluindo a linha entre
                  colchetes</strong>. Contém token e chave — vai cifrada para o cofre e é
                  materializada em arquivo temporário (modo 0600) só durante o envio.
                </div>
              </div>

              <div className="field">
                <label className="label">Parâmetros extras do rclone</label>
                <input
                  className="mono"
                  value={f.rclone_flags}
                  onChange={set("rclone_flags")}
                  placeholder="--drive-chunk-size 64M --bwlimit 20M"
                />
                <div className="field-help">
                  Opcional. Útil para limitar banda (<span className="mono">--bwlimit</span>)
                  ou ajustar o tamanho do bloco em arquivo grande.
                </div>
              </div>
            </>
          )}

          <div className="row row-2">
            <div className="field">
              <label className="label">Retenção (dias)</label>
              <input
                type="number"
                min={0}
                max={3650}
                value={f.retencao_dias}
                onChange={set("retencao_dias")}
              />
              <div className="field-help">
                {f.tipo === "local"
                  ? "Artefatos mais velhos que isso são apagados. 0 = nunca apagar."
                  : "A limpeza automática só age em destino local. Em nuvem, use a política de ciclo de vida do provedor."}
              </div>
            </div>
            <div className="field">
              <label className="label">Opções</label>
              <label className="check">
                <input type="checkbox" checked={f.padrao} onChange={set("padrao")} />
                <span>Pré-selecionado ao criar backup e agendamento</span>
              </label>
              <label className="check">
                <input type="checkbox" checked={f.enabled} onChange={set("enabled")} />
                <span>Destino ativo</span>
              </label>
            </div>
          </div>

          {tipoInfo && (
            <div className="small muted" style={{ marginTop: 6 }}>
              Depois de salvar, use <strong>Testar</strong>: o painel grava um arquivo
              pequeno, confere e apaga. Permissão de escrita e cota só aparecem na hora
              de gravar — melhor descobrir agora que no meio do backup.
            </div>
          )}
        </div>

        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>
            Cancelar
          </button>
          <button className="btn btn-primary" disabled={enviando}>
            {enviando ? "Salvando…" : editando ? "Salvar" : "Cadastrar"}
          </button>
        </div>
      </form>
    </div>
  );
}
