import React, { useCallback, useEffect, useState } from "react";
import { api, formatData } from "../../api";
import { t } from "../../i18n";
import { useSessao } from "../../usePermissions";
import {
  fecharSeForaLimpo, Carregando, Erro,
} from "../Comuns";
import { IconAlerta, IconLixeira, IconMais, IconOk } from "../Icons";

/**
 * O que cada perfil pode ver e fazer.
 *
 * Vem inteiro do backend (`/auth/perfis`), que monta a matriz a partir do
 * mesmo catálogo que o servidor usa para AUTORIZAR. Se esta tela tivesse
 * a própria cópia da lista, ela diria uma coisa e o servidor faria outra
 * — e a divergência apareceria só quando alguém reclamasse de um botão
 * que sumiu.
 *
 * Organizada por ÁREA, e não em lista plana de 23 códigos: a pergunta
 * real de quem cadastra alguém é "essa pessoa vai poder mexer em
 * backup?", não "essa pessoa tem backups.restore?".
 */
function MatrizPerfis() {
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [aberta, setAberta] = useState(null);

  useEffect(() => {
    api.perfis().then(setDados).catch((ex) => setErro(ex.message));
  }, []);

  if (erro) {
    return (
      <div className="card">
        <div className="section-title" style={{ marginBottom: 10 }}>
          {t("O que cada perfil pode")}
        </div>
        <Erro mensagem={erro} />
      </div>
    );
  }
  if (!dados) return <Carregando />;

  const perfis = dados.perfis || [];
  const areas = dados.areas || [];

  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 4 }}>
        {t("O que cada perfil pode")}
      </div>
      <div className="small muted" style={{ marginBottom: 14 }}>
        {t("Os perfis são fixos. Para dar mais acesso a alguém, troque o perfil da pessoa.")}
      </div>

      {/* Os quatro perfis, com para quem serve e o que NÃO pode. O que
          não pode costuma ser mais decisivo que o que pode. */}
      <div
        className="row"
        style={{
          gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
          marginBottom: 18,
        }}
      >
        {perfis.map((p) => (
          <div
            key={p.codigo}
            className="card card-tight"
            style={{ background: "var(--bg-2)" }}
          >
            <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 4 }}>
              <strong style={{ color: "var(--titulo)" }}>{p.rotulo}</strong>
              <span className="pill pill-idle" title={t("Permissões concedidas")}>
                {p.total}
              </span>
            </div>
            <div className="small" style={{ marginBottom: 6 }}>{p.resumo}</div>
            <div className="small muted" style={{ marginBottom: 6 }}>
              <strong>{t("Para quem")}:</strong> {p.para_quem}
            </div>
            <div className="small muted">
              <strong>{t("Não pode")}:</strong> {p.nao_pode}
            </div>
            {p.destrutivas > 0 && (
              <div className="small" style={{ color: "var(--amber-fg)", marginTop: 6 }}>
                <IconAlerta size={12} /> {p.destrutivas} {t("ação(ões) destrutiva(s)")}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* A matriz. Uma seção por área, aberta uma de cada vez: 23 linhas
          de uma vez é uma parede, e parede ninguém lê. */}
      {areas.map((a) => {
        const abertaAqui = aberta === a.chave;
        return (
          <div key={a.chave} style={{ marginBottom: 10 }}>
            <button
              type="button"
              className="link-inline"
              onClick={() => setAberta(abertaAqui ? null : a.chave)}
              style={{ width: "100%" }}
            >
              <div
                className="stack-h"
                style={{
                  justifyContent: "space-between",
                  padding: "8px 10px",
                  background: "var(--bg-2)",
                  borderRadius: "var(--radius)",
                }}
              >
                <div>
                  <strong>{a.rotulo}</strong>
                  <div className="small muted">{a.ajuda}</div>
                </div>
                <span className="small muted">
                  {a.itens.length} · {abertaAqui ? t("esconder") : t("ver")}
                </span>
              </div>
            </button>

            {abertaAqui && (
              <div className="table-wrap" style={{ marginTop: 8 }}>
                <table>
                  <thead>
                    <tr>
                      <th style={{ minWidth: 260 }}>{t("O que permite")}</th>
                      {perfis.map((p) => (
                        <th key={p.codigo} className="right" style={{ whiteSpace: "nowrap" }}>
                          {p.rotulo.split(" ")[0]}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {a.itens.map((item) => (
                      <tr key={item.codigo}>
                        <td className="small">
                          <div className="stack-h" style={{ gap: 6 }}>
                            <strong>{item.rotulo}</strong>
                            {item.destrutiva && (
                              <span
                                className="pill pill-err"
                                title={t("Pede confirmação digitada e vira auditoria crítica")}
                              >
                                {t("destrutiva")}
                              </span>
                            )}
                          </div>
                          {item.detalhe && (
                            <div className="muted" style={{ marginTop: 2 }}>{item.detalhe}</div>
                          )}
                          <div className="mono muted" style={{ fontSize: 11, marginTop: 2 }}>
                            {item.codigo}
                          </div>
                        </td>
                        {perfis.map((p) => (
                          <td key={p.codigo} className="right">
                            {item.perfis.includes(p.codigo) ? (
                              <span style={{ color: "var(--green-fg)" }} title={t("permitido")}>
                                <IconOk size={14} />
                              </span>
                            ) : (
                              <span className="muted" title={t("não permitido")}>—</span>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}

      <div className="small muted" style={{ marginTop: 12 }}>
        {t("Botão sem permissão não aparece na tela.")}
      </div>
    </div>
  );
}

export default function UsuariosView() {
  const { usuario: eu } = useSessao();
  const [lista, setLista] = useState([]);
  const [perfis, setPerfis] = useState({});
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [editando, setEditando] = useState(null);

  const carregar = useCallback(async () => {
    setErro("");
    try {
      const [usuarios, catalogo] = await Promise.all([api.usuarios(), api.catalogo()]);
      setLista(usuarios);
      // O catálogo de permissões não é lido aqui: quem explica o que
      // cada perfil pode é `MatrizPerfis`, direto de /auth/perfis.
      setPerfis(catalogo.perfis);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function remover(u) {
    if (!window.confirm(`Remover o usuário '${u.username}'?`)) return;
    try {
      await api.removerUsuario(u.id);
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    }
  }

  async function alternarAtivo(u) {
    try {
      await api.atualizarUsuario(u.id, { is_active: !u.is_active });
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
          <div className="page-title">{t("tela.usuarios")}</div>
          <div className="page-sub">
            {t("tela.usuarios.sub")}
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-primary" onClick={() => setEditando({})}>
            <IconMais size={15} /> {t("Novo usuário")}</button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      <div className="stack-v">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("Usuário")}</th>
                <th>{t("Nome")}</th>
                <th>{t("Perfil")}</th>
                <th>{t("Situação")}</th>
                <th>{t("Último acesso")}</th>
                <th style={{ width: 1 }}></th>
              </tr>
            </thead>
            <tbody>
              {lista.map((u) => (
                <tr key={u.id} style={{ opacity: u.is_active ? 1 : 0.55 }}>
                  <td>
                    <span className="mono">{u.username}</span>
                    {u.is_super_admin && (
                      <span className="pill pill-info" style={{ marginLeft: 6 }}>super</span>
                    )}
                    {u.id === eu.id && (
                      <span className="pill pill-idle" style={{ marginLeft: 6 }}>{t("você")}</span>
                    )}
                  </td>
                  <td>{u.full_name || <span className="muted">—</span>}</td>
                  <td className="small">{perfis[u.role] || u.role}</td>
                  <td>
                    <span className={`pill ${u.is_active ? "pill-ok" : "pill-idle"}`}>
                      {u.is_active ? "ativo" : "inativo"}
                    </span>
                    {u.senha_padrao && (
                      <div className="small" style={{ color: "var(--amber)", marginTop: 3 }}>{t("senha de fábrica")}</div>
                    )}
                  </td>
                  <td className="small muted">{formatData(u.last_login_at)}</td>
                  <td>
                    <div className="stack-h" style={{ gap: 6, flexWrap: "nowrap" }}>
                      <button className="btn btn-secondary btn-sm" onClick={() => setEditando(u)}>{t("Editar")}</button>
                      {u.id !== eu.id && (
                        <>
                          <button className="btn btn-secondary btn-sm" onClick={() => alternarAtivo(u)}>
                            {u.is_active ? "Desativar" : "Ativar"}
                          </button>
                          {!u.is_super_admin && (
                            <button className="btn btn-danger btn-sm" onClick={() => remover(u)}>
                              <IconLixeira size={13} />
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <MatrizPerfis />
      </div>

      {editando && (
        <ModalUsuario
          inicial={editando}
          perfis={perfis}
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

function ModalUsuario({ inicial, perfis, onFechar, onPronto }) {
  const editando = Boolean(inicial.id);
  const [username, setUsername] = useState(inicial.username || "");
  const [fullName, setFullName] = useState(inicial.full_name || "");
  const [role, setRole] = useState(inicial.role || "observador");
  const [senha, setSenha] = useState("");
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  async function enviar(e) {
    e.preventDefault();
    setErro("");
    setEnviando(true);
    try {
      if (editando) {
        const corpo = { full_name: fullName, role };
        if (senha) corpo.password = senha;
        await api.atualizarUsuario(inicial.id, corpo);
      } else {
        await api.criarUsuario({
          username,
          full_name: fullName,
          role,
          password: senha,
        });
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
            {editando ? `Editar ${inicial.username}` : "Novo usuário"}
          </div>
        </div>
        <div className="modal-body">
          {erro && <div className="login-err">{erro}</div>}

          <div className="row row-2">
            <div className="field">
              <label className="label label-required">{t("Usuário")}</label>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={editando}
                required
              />
            </div>
            <div className="field">
              <label className="label">{t("Nome completo")}</label>
              <input value={fullName} onChange={(e) => setFullName(e.target.value)} />
            </div>
          </div>

          <div className="field">
            <label className="label label-required">{t("Perfil")}</label>
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {Object.entries(perfis).map(([id, nome]) => (
                <option key={id} value={id}>{nome}</option>
              ))}
            </select>
            <div className="field-help">
              <strong>{t("Observador")}</strong> vê tudo e não executa nada.{" "}
              <strong>{t("Operador")}</strong> reinicia container e dispara backup.{" "}
              <strong>{t("Técnico")}</strong> soma terminal com sudo e agendamentos.{" "}
              <strong>{t("Administrador")}</strong> {t("tem tudo, inclusive restore e parada do stack.")}</div>
          </div>

          <div className="field">
            <label className={`label ${editando ? "" : "label-required"}`}>{t("Senha")}</label>
            <input
              type="password"
              value={senha}
              onChange={(e) => setSenha(e.target.value)}
              autoComplete="new-password"
              minLength={6}
              required={!editando}
              placeholder={editando ? "Deixe em branco para manter" : "Mínimo 6 caracteres"}
            />
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>{t("Cancelar")}</button>
          <button className="btn btn-primary" disabled={enviando}>
            {enviando ? "Salvando…" : editando ? "Salvar" : "Criar"}
          </button>
        </div>
      </form>
    </div>
  );
}
