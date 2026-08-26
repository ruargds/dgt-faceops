import React, { useCallback, useEffect, useState } from "react";
import { api, formatData } from "../../api";
import { t } from "../../i18n";
import { useSessao } from "../../usePermissions";
import {
  fecharSeForaLimpo, Carregando, Erro,
} from "../Comuns";
import { IconLixeira, IconMais } from "../Icons";

export default function UsuariosView() {
  const { usuario: eu } = useSessao();
  const [lista, setLista] = useState([]);
  const [perfis, setPerfis] = useState({});
  const [permissoes, setPermissoes] = useState({});
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [editando, setEditando] = useState(null);

  const carregar = useCallback(async () => {
    setErro("");
    try {
      const [usuarios, catalogo] = await Promise.all([api.usuarios(), api.catalogo()]);
      setLista(usuarios);
      setPerfis(catalogo.perfis);
      setPermissoes(catalogo.permissoes);
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
            <IconMais size={15} /> Novo usuário
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      <div className="stack-v">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Usuário</th>
                <th>Nome</th>
                <th>Perfil</th>
                <th>Situação</th>
                <th>Último acesso</th>
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
                      <span className="pill pill-idle" style={{ marginLeft: 6 }}>você</span>
                    )}
                  </td>
                  <td>{u.full_name || <span className="muted">—</span>}</td>
                  <td className="small">{perfis[u.role] || u.role}</td>
                  <td>
                    <span className={`pill ${u.is_active ? "pill-ok" : "pill-idle"}`}>
                      {u.is_active ? "ativo" : "inativo"}
                    </span>
                    {u.senha_padrao && (
                      <div className="small" style={{ color: "var(--amber)", marginTop: 3 }}>
                        senha de fábrica
                      </div>
                    )}
                  </td>
                  <td className="small muted">{formatData(u.last_login_at)}</td>
                  <td>
                    <div className="stack-h" style={{ gap: 6, flexWrap: "nowrap" }}>
                      <button className="btn btn-secondary btn-sm" onClick={() => setEditando(u)}>
                        Editar
                      </button>
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

        <div className="card">
          <div className="section-title">O que cada perfil pode fazer</div>
          <div className="small muted" style={{ marginBottom: 12 }}>
            As permissões são fixas no código, não em tabela. Botão sem permissão não
            aparece na tela — não fica cinza.
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Permissão</th>
                  <th>O que libera</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(permissoes).map(([codigo, texto]) => (
                  <tr key={codigo}>
                    <td className="mono small">{codigo}</td>
                    <td className="small">{texto}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
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
              <label className="label label-required">Usuário</label>
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={editando}
                required
              />
            </div>
            <div className="field">
              <label className="label">Nome completo</label>
              <input value={fullName} onChange={(e) => setFullName(e.target.value)} />
            </div>
          </div>

          <div className="field">
            <label className="label label-required">Perfil</label>
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              {Object.entries(perfis).map(([id, nome]) => (
                <option key={id} value={id}>{nome}</option>
              ))}
            </select>
            <div className="field-help">
              <strong>Observador</strong> vê tudo e não executa nada.{" "}
              <strong>Operador</strong> reinicia container e dispara backup.{" "}
              <strong>Técnico</strong> soma terminal com sudo e agendamentos.{" "}
              <strong>Administrador</strong> tem tudo, inclusive restore e parada do stack.
            </div>
          </div>

          <div className="field">
            <label className={`label ${editando ? "" : "label-required"}`}>Senha</label>
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
          <button type="button" className="btn btn-secondary" onClick={onFechar}>Cancelar</button>
          <button className="btn btn-primary" disabled={enviando}>
            {enviando ? "Salvando…" : editando ? "Salvar" : "Criar"}
          </button>
        </div>
      </form>
    </div>
  );
}
