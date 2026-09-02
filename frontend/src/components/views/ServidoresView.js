import React, { useCallback, useEffect, useState } from "react";
import { api, formatData } from "../../api";
import { t } from "../../i18n";
import { usePermissions } from "../../usePermissions";
import {
  fecharSeForaLimpo, Carregando, Erro, Vazio,
} from "../Comuns";
import { IconAlerta, IconChave, IconGPU, IconLixeira, IconMais, IconOk } from "../Icons";

const PAPEIS = [
  { id: "appserver", nome: "Aplicação" },
  { id: "dbserver", nome: "Banco de dados" },
  { id: "extraction", nome: "Extração / GPU" },
  { id: "ftpserver", nome: "FTP / arquivos" },
  { id: "outro", nome: "Outro" },
];

export default function ServidoresView({ alvo }) {
  const { has } = usePermissions();
  const [lista, setLista] = useState([]);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [editando, setEditando] = useState(null);
  const [testes, setTestes] = useState({});

  const carregar = useCallback(async () => {
    setErro("");
    try {
      setLista(await api.hosts());
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  // Chegou de um alerta de "sem comunicação" — rola até o servidor certo em
  // vez de deixar a pessoa procurar na lista.
  useEffect(() => {
    if (!alvo || !alvo.hostId || carregando) return;
    const el = document.getElementById(`servidor-${alvo.hostId}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [alvo, carregando]);

  async function testar(h) {
    setTestes((t) => ({ ...t, [h.id]: { carregando: true } }));
    try {
      const r = await api.testarHost(h.id);
      setTestes((t) => ({ ...t, [h.id]: { ...r, carregando: false } }));
      await carregar();
    } catch (ex) {
      setTestes((t) => ({ ...t, [h.id]: { ok: false, erro: ex.message, carregando: false } }));
    }
  }

  async function remover(h) {
    if (!window.confirm(`Remover '${h.name}'? O histórico de backups também será apagado.`)) return;
    try {
      await api.removerHost(h.id);
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
          <div className="page-title">{t("tela.servidores")}</div>
          <div className="page-sub">
            {t("tela.servidores.sub")}
          </div>
        </div>
        {has("hosts.manage") && (
          <div className="page-actions">
            <button className="btn btn-primary" onClick={() => setEditando({})}>
              <IconMais size={15} /> {t("Cadastrar servidor")}</button>
          </div>
        )}
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {lista.length === 0 ? (
        <Vazio titulo={t("Nenhum servidor cadastrado")}>
          Cadastre as VMs do FindFace Multi. O painel lê a chave pública do servidor
          antes de guardar qualquer credencial.
        </Vazio>
      ) : (
        <div className="grid-cards">
          {lista.map((h) => {
            const teste = testes[h.id];
            return (
              <div
                className="card"
                id={`servidor-${h.id}`}
                key={h.id}
                style={{
                  opacity: h.enabled ? 1 : 0.6,
                  borderColor: alvo && alvo.hostId === h.id ? "var(--blue)" : undefined,
                  boxShadow: alvo && alvo.hostId === h.id ? "0 0 0 3px rgba(26,111,196,.10)" : undefined,
                }}
              >
                <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 4 }}>
                  <div>
                    <strong style={{ fontSize: 15, color: "var(--titulo)" }}>
                      {h.rotulo || h.name}
                    </strong>
                    {h.alias && h.alias !== h.name && (
                      <div className="nome-tecnico">{h.name}</div>
                    )}
                  </div>
                  <div className="stack-h" style={{ gap: 5 }}>
                    {h.has_gpu && (
                      <span className="pill pill-info"><IconGPU size={12} /> {t("GPU")}</span>
                    )}
                    {!h.enabled && <span className="pill pill-idle">desativado</span>}
                  </div>
                </div>

                <div className="small muted">{h.description || PAPEIS.find((p) => p.id === h.role)?.nome}</div>

                <div className="small mono" style={{ margin: "10px 0 6px" }}>
                  {h.ssh_user}@{h.address}:{h.ssh_port}
                </div>

                <div className="stack-h small muted" style={{ gap: 6, marginBottom: 10 }}>
                  <IconChave size={13} />
                  {h.auth_method === "key" ? "chave PEM" : "senha"}
                  {h.tem_sudo && " · sudo guardado"}
                </div>

                <div className="small muted" style={{ marginBottom: 10, wordBreak: "break-all" }}>
                  Identidade fixada:{" "}
                  <span className="mono">{h.host_key_fingerprint || "—"}</span>
                </div>

                {h.last_status === "erro" && h.last_error && (
                  <div className="small" style={{ color: "var(--red)", marginBottom: 10 }}>
                    <IconAlerta size={12} /> {h.last_error.slice(0, 160)}
                  </div>
                )}

                <div className="small muted" style={{ marginBottom: 12 }}>
                  Último contato: {formatData(h.last_seen_at)}
                </div>

                {teste && !teste.carregando && (
                  <div
                    className="card card-tight"
                    style={{
                      marginBottom: 12,
                      background: teste.ok ? "var(--green-bg)" : "var(--red-bg)",
                      borderColor: teste.ok ? "var(--green-bd)" : "var(--red-bd)",
                    }}
                  >
                    {teste.ok ? (
                      <div className="small" style={{ color: "var(--green-fg)" }}>
                        <div className="stack-h"><IconOk size={13} /> Conectado em {teste.latencia_ms} ms</div>
                        <div className="mono" style={{ marginTop: 4 }}>
                          {teste.usuario}@{teste.hostname} · kernel {teste.kernel}
                        </div>
                        <div style={{ marginTop: 4 }}>
                          sudo: {teste.sudo ? "sim" : "NÃO"} · docker:{" "}
                          {teste.docker_presente ? "sim" : "NÃO"} · FindFace:{" "}
                          {teste.findface_presente ? "sim" : "NÃO"} · GPU:{" "}
                          {teste.gpu_presente ? "sim" : "não"}
                        </div>
                        {!teste.sudo && (
                          <div style={{ marginTop: 4, color: "var(--amber-fg)" }}>{t("Sem sudo o backup e o restart de container não funcionam.")}</div>
                        )}
                        {teste.caminho_corrigido && (
                          <div style={{ marginTop: 4 }}>
                            Caminho do FindFace corrigido por detecção:{" "}
                            <span className="mono">{teste.ffmulti_dir}</span>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="small" style={{ color: "var(--red-fg)" }}>{teste.erro}</div>
                    )}
                  </div>
                )}

                <div className="stack-h" style={{ gap: 6 }}>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => testar(h)}
                    disabled={teste && teste.carregando}
                  >
                    {teste && teste.carregando ? "Testando…" : "Testar conexão"}
                  </button>
                  {has("hosts.manage") && (
                    <>
                      <button className="btn btn-secondary btn-sm" onClick={() => setEditando(h)}>{t("Editar")}</button>
                      <button className="btn btn-danger btn-sm" onClick={() => remover(h)}>
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
        <ModalServidor
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

function ModalServidor({ inicial, onFechar, onPronto }) {
  const editando = Boolean(inicial.id);
  const [f, setF] = useState({
    name: inicial.name || "",
    alias: inicial.alias || "",
    description: inicial.description || "",
    role: inicial.role || "outro",
    address: inicial.address || "",
    ssh_port: inicial.ssh_port || 22,
    ssh_user: inicial.ssh_user || "",
    auth_method: inicial.auth_method || "key",
    ffmulti_dir: inicial.ffmulti_dir || "/opt/findface-multi",
    compose_file: inicial.compose_file || "/opt/findface-multi/docker-compose.yaml",
    has_gpu: inicial.has_gpu || false,
    enabled: inicial.enabled !== undefined ? inicial.enabled : true,
    ff_api_url: inicial.ff_api_url || "",
    ff_api_user: inicial.ff_api_user || "",
  });
  const [segredos, setSegredos] = useState({
    ssh_key: "",
    ssh_key_passphrase: "",
    ssh_password: "",
    sudo_password: "",
    ff_api_pass: "",
    ff_api_token: "",
  });
  const [testeApi, setTesteApi] = useState(null);

  async function testarApi() {
    setTesteApi({ carregando: true });
    try {
      const r = await api.testarApiHost(inicial.id, {
        ff_api_url: f.ff_api_url,
        ff_api_user: f.ff_api_user || undefined,
        ff_api_pass: segredos.ff_api_pass || undefined,
        ff_api_token: segredos.ff_api_token || undefined,
      });
      setTesteApi(r);
    } catch (ex) {
      setTesteApi({ ok: false, erro: ex.message });
    }
  }
  const [chaveHost, setChaveHost] = useState(null);
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  const set = (campo) => (e) => {
    const valor = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setF((atual) => ({ ...atual, [campo]: valor }));
  };
  const setSeg = (campo) => (e) =>
    setSegredos((atual) => ({ ...atual, [campo]: e.target.value }));

  async function lerChaveHost() {
    setErro("");
    setChaveHost(null);
    try {
      const r = await api.scanChave(f.address, Number(f.ssh_port));
      setChaveHost(r);
    } catch (ex) {
      setErro(ex.message);
    }
  }

  async function enviar(e) {
    e.preventDefault();
    setErro("");
    setEnviando(true);

    // Só envia segredo que foi realmente digitado. Mandar string vazia
    // apagaria a chave já guardada no cofre.
    const corpo = { ...f, ssh_port: Number(f.ssh_port) };
    Object.entries(segredos).forEach(([k, v]) => {
      if (v) corpo[k] = v;
    });

    try {
      if (editando) {
        await api.atualizarHost(inicial.id, corpo);
      } else {
        await api.criarHost(corpo);
      }
      await onPronto();
    } catch (ex) {
      setErro(ex.message);
      setEnviando(false);
    }
  }

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <form className="modal modal-wide" onClick={(e) => e.stopPropagation()} onSubmit={enviar}>
        <div className="modal-head">
          <div className="modal-title">
            {editando ? `Editar ${inicial.name}` : "Cadastrar servidor"}
          </div>
        </div>

        <div className="modal-body">
          {erro && <div className="login-err">{erro}</div>}

          <div className="section-title">{t("Identificação")}</div>
          <div className="row row-3 form-linha">
            <div className="field">
              <label className="label label-required">{t("Nome")}</label>
              <input value={f.name} onChange={set("name")} placeholder="vm-appserver" required />
            </div>
            <div className="field">
              <label className="label">{t("Apelido")}</label>
              <input value={f.alias} onChange={set("alias")} placeholder={f.name || "Servidor da portaria"} />
            </div>
            <div className="field">
              <label className="label">{t("Papel")}</label>
              <select value={f.role} onChange={set("role")}>
                {PAPEIS.map((p) => (
                  <option key={p.id} value={p.id}>{p.nome}</option>
                ))}
              </select>
            </div>
          </div>
          {/* O nome é identidade: vai no alvo da auditoria e no caminho
              dos artefatos de backup no disco. O apelido é só rótulo —
              trocá-lo não move nem renomeia nada. */}
          <div className="form-ajuda" style={{ marginBottom: 14 }}>
            {t("O apelido é o que aparece nas telas e nos avisos do Telegram. O nome continua sendo a identidade técnica do servidor — em auditoria, nos arquivos de backup e na confirmação de ações destrutivas.")}
          </div>
          <div className="field">
            <label className="label">{t("Descrição")}</label>
            <input value={f.description} onChange={set("description")} />
          </div>

          <div className="section-title" style={{ marginTop: 18 }}>{t("Acesso SSH")}</div>
          <div className="row row-3">
            <div className="field">
              <label className="label label-required">Endereço (IP ou DNS)</label>
              <input value={f.address} onChange={set("address")} required />
            </div>
            <div className="field">
              <label className="label label-required">{t("Porta")}</label>
              <input type="number" value={f.ssh_port} onChange={set("ssh_port")} required />
            </div>
            <div className="field">
              <label className="label label-required">{t("Usuário")}</label>
              <input value={f.ssh_user} onChange={set("ssh_user")} placeholder="azureuser" required />
            </div>
          </div>

          <div className="field">
            <div className="stack-h" style={{ justifyContent: "space-between" }}>
              <label className="label" style={{ marginBottom: 0 }}>{t("Identidade do servidor")}</label>
              <button type="button" className="btn btn-secondary btn-sm" onClick={lerChaveHost}>{t("Ler chave do servidor")}</button>
            </div>
            <div className="field-help" style={{ marginTop: 6 }}>{t("O painel lê a chave pública do servidor")} <strong>antes</strong> de qualquer
              credencial trafegar, e fixa essa identidade. Se a chave mudar depois, a
              conexão é recusada em vez de entregar a senha de sudo a um impostor.
              {editando && " Ao salvar, a leitura é refeita se o endereço mudar."}
            </div>
            {chaveHost && (
              <div
                className="card card-tight"
                style={{ marginTop: 8, background: "var(--green-bg)", borderColor: "var(--green-bd)" }}
              >
                <div className="small mono" style={{ color: "var(--green-fg)", wordBreak: "break-all" }}>
                  {chaveHost.fingerprint}
                </div>
              </div>
            )}
            {editando && inicial.host_key_fingerprint && !chaveHost && (
              <div className="small mono muted" style={{ marginTop: 8, wordBreak: "break-all" }}>
                Atual: {inicial.host_key_fingerprint}
              </div>
            )}
          </div>

          <div className="field">
            <label className="label">{t("Autenticação")}</label>
            <select value={f.auth_method} onChange={set("auth_method")}>
              <option value="key">{t("Chave PEM")}</option>
              <option value="password">{t("Senha")}</option>
            </select>
          </div>

          {f.auth_method === "key" ? (
            <>
              <div className="field">
                <div className="stack-h" style={{ justifyContent: "space-between", alignItems: "flex-end" }}>
                  <label className={`label ${editando ? "" : "label-required"}`} style={{ marginBottom: 0 }}>
                    Chave privada (PEM)
                  </label>
                  <label className="btn btn-secondary btn-sm" style={{ cursor: "pointer", margin: 0 }}>{t("Carregar arquivo .pem")}<input
                      type="file"
                      style={{ display: "none" }}
                      onChange={(e) => {
                        const arq = e.target.files && e.target.files[0];
                        if (!arq) return;
                        // Lê o arquivo NO NAVEGADOR e joga no campo. A chave
                        // nunca sobe como arquivo: segue o mesmo caminho da
                        // colagem — vai cifrada no corpo JSON ao salvar.
                        const leitor = new FileReader();
                        leitor.onload = (ev) => {
                          const txt = String(ev.target.result || "").trim();
                          if (!txt.includes("PRIVATE KEY")) {
                            setErro(
                              "O arquivo não parece uma chave privada (falta a linha " +
                                "BEGIN ... PRIVATE KEY). Se for .ppk do PuTTY, converta " +
                                "para OpenSSH antes."
                            );
                            return;
                          }
                          setErro("");
                          setSegredos((a) => ({ ...a, ssh_key: txt }));
                        };
                        leitor.readAsText(arq);
                        e.target.value = "";
                      }}
                    />
                  </label>
                </div>
                <textarea
                  rows={6}
                  value={segredos.ssh_key}
                  onChange={setSeg("ssh_key")}
                  placeholder={
                    editando
                      ? "Deixe em branco para manter a chave já guardada"
                      : "-----BEGIN OPENSSH PRIVATE KEY-----\n…  (ou use Carregar arquivo)"
                  }
                  required={!editando}
                  style={{ marginTop: 6 }}
                />
                <div className="field-help">{t("Cole a chave ou carregue o arquivo")} <span className="mono">.pem</span>.
                  Guardada cifrada (Fernet AES-128) no cofre. Nunca é exibida nem
                  devolvida pela API depois de salva.
                  {editando && inicial.key_fingerprint && (
                    <>
                      {" "}Impressão atual: <span className="mono">{inicial.key_fingerprint}</span>
                    </>
                  )}
                </div>
              </div>
              <div className="field">
                <label className="label">Senha da chave (se tiver)</label>
                <input
                  type="password"
                  value={segredos.ssh_key_passphrase}
                  onChange={setSeg("ssh_key_passphrase")}
                  autoComplete="new-password"
                />
              </div>
            </>
          ) : (
            <div className="field">
              <label className={`label ${editando ? "" : "label-required"}`}>{t("Senha SSH")}</label>
              <input
                type="password"
                value={segredos.ssh_password}
                onChange={setSeg("ssh_password")}
                autoComplete="new-password"
                required={!editando}
                placeholder={editando ? "Deixe em branco para manter" : ""}
              />
            </div>
          )}

          <div className="field">
            <label className="label">{t("Senha de sudo")}</label>
            <input
              type="password"
              value={segredos.sudo_password}
              onChange={setSeg("sudo_password")}
              autoComplete="new-password"
              placeholder={editando ? "Deixe em branco para manter" : ""}
            />
            <div className="field-help">{t("Deixe vazio se o usuário tem")} <span className="mono">{t("NOPASSWD")}</span> no
              sudoers. Backup e restart de container precisam de root.
            </div>
          </div>

          <div className="section-title" style={{ marginTop: 18 }}>{t("FindFace Multi")}</div>
          <div className="row row-2">
            <div className="field">
              <label className="label">{t("Diretório de instalação")}</label>
              <input className="mono" value={f.ffmulti_dir} onChange={set("ffmulti_dir")} />
            </div>
            <div className="field">
              <label className="label">{t("Arquivo docker-compose")}</label>
              <input className="mono" value={f.compose_file} onChange={set("compose_file")} />
            </div>
          </div>

          <div className="section-title" style={{ marginTop: 18 }}>{t("API do FindFace")} <span className="small muted">— coleta de câmeras por IP (recomendado)</span>
          </div>
          <div className="field-help" style={{ marginBottom: 8 }}>
            Caminho preferido para contar câmeras, eventos e licenças: o painel
            usa a API do FindFace em vez de vasculhar o banco por SSH. Entre com
            o mesmo usuário e senha que você usa na plataforma da NtechLab. Se
            ficar em branco, o painel cai no SSH+PostgreSQL — e o licenciamento,
            que só existe pela API, fica indisponível.
          </div>
          <div className="field">
            <label className="label">{t("URL da API")}</label>
            <input
              className="mono"
              value={f.ff_api_url}
              onChange={set("ff_api_url")}
              placeholder="https://10.50.153.10/api"
            />
          </div>
          <div className="field">
            <label className="label">{t("Usuário da API")}</label>
            <input
              value={f.ff_api_user}
              onChange={set("ff_api_user")}
              autoComplete="off"
              placeholder={t("o mesmo usuário que entra no FindFace")}
            />
          </div>
          <div className="field">
            <label className="label">{t("Senha da API")}</label>
            <input
              type="password"
              value={segredos.ff_api_pass}
              onChange={setSeg("ff_api_pass")}
              autoComplete="new-password"
              placeholder={
                editando && inicial.tem_api
                  ? "Deixe em branco para manter"
                  : "senha desse usuário no FindFace"
              }
            />
            <div className="field-help">
              O painel faz login na API e reaproveita a sessão por 20 minutos —
              não repete o login a cada leitura. Guardada cifrada no cofre;
              nunca é exibida depois de salva.
            </div>
          </div>
          <div className="field">
            <label className="label">{t("Token da API")} <span className="small muted">— opcional</span>
            </label>
            <input
              type="password"
              value={segredos.ff_api_token}
              onChange={setSeg("ff_api_token")}
              autoComplete="new-password"
              placeholder={editando && inicial.tem_api ? "Deixe em branco para manter" : "cole o token de API do FindFace"}
            />
            <div className="field-help">
              Só para instalação que gere token de API. Preenchido, ele substitui
              o usuário e a senha acima — não expira com a sessão.
            </div>
          </div>
          {editando && (
            <div className="stack-h" style={{ gap: 10, alignItems: "center" }}>
              <button type="button" className="btn btn-secondary btn-sm" onClick={testarApi} disabled={testeApi && testeApi.carregando}>
                {testeApi && testeApi.carregando ? "Testando…" : "Testar API"}
              </button>
              {testeApi && !testeApi.carregando && testeApi.ok && (
                <span className="small" style={{ color: "var(--green)" }}>
                  OK — {testeApi.usuario ? `${testeApi.usuario} · ` : ""}
                  {testeApi.cameras != null ? `${testeApi.cameras} câmera(s)` : "conectou"}
                </span>
              )}
              {/* O teste usa o que está DIGITADO; as telas usam o que está
                  SALVO. Sem este aviso, testar e esquecer de salvar dá OK
                  aqui e credencial recusada em toda leitura depois. */}
              {testeApi &&
                !testeApi.carregando &&
                (testeApi.origem === "digitada" || testeApi.origem === "token digitado") && (
                  <span className="small" style={{ color: "var(--amber-fg)" }}>
                    testado com a credencial digitada — <strong>salve</strong> o
                    servidor para as telas usarem esta credencial
                  </span>
                )}
              {testeApi && !testeApi.carregando && testeApi.ok === false && (
                <span className="small" style={{ color: "var(--red)" }}>{testeApi.erro}</span>
              )}
            </div>
          )}

          <label className="check">
            <input type="checkbox" checked={f.has_gpu} onChange={set("has_gpu")} />
            <span>Este servidor tem GPU (detectado sozinho no teste de conexão)</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={f.enabled} onChange={set("enabled")} />
            <span>Servidor ativo (desmarque para suspender coletas e agendamentos)</span>
          </label>
        </div>

        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>{t("Cancelar")}</button>
          <button className="btn btn-primary" disabled={enviando}>
            {enviando ? "Salvando…" : editando ? "Salvar" : "Cadastrar"}
          </button>
        </div>
      </form>
    </div>
  );
}
