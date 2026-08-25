import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { usePermissions } from "../../usePermissions";
import { Carregando, Erro } from "../Comuns";
import { IconAtualizar, IconOk } from "../Icons";

/**
 * A tela se monta a partir do catálogo do backend.
 *
 * Adicionar uma opção nova é uma linha em `config_service.py` — rótulo,
 * tipo, validação e ajuda vêm de lá. Nada aqui precisa mudar. Foi feito
 * assim porque configuração cresce com o projeto, e ter que editar duas
 * pontas para cada campo novo garante que uma delas fica para trás.
 */
export default function ConfiguracoesView() {
  const { has } = usePermissions();
  const podeEditar = has("users.manage");

  const [grupos, setGrupos] = useState([]);
  const [rascunho, setRascunho] = useState({});
  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [salvando, setSalvando] = useState(false);

  const carregar = useCallback(async () => {
    setErro("");
    try {
      const r = await api.config();
      setGrupos(r.grupos);
      setRascunho({});
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  function alterar(chave, valor) {
    setRascunho((a) => ({ ...a, [chave]: valor }));
    setAviso("");
  }

  const pendentes = Object.keys(rascunho);

  async function salvar() {
    if (!pendentes.length) return;
    setSalvando(true);
    setErro("");
    try {
      await api.salvarConfig(rascunho);
      setAviso(
        `${pendentes.length} opção(ões) salva(s). Alterações valem imediatamente ` +
          "— nada precisa reiniciar."
      );
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setSalvando(false);
    }
  }

  async function restaurar(chave) {
    try {
      await api.restaurarConfig(chave);
      setAviso("Opção restaurada ao padrão.");
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
          <div className="page-title">Configurações</div>
          <div className="page-sub">
            Ajustes do painel — valem na hora, sem reiniciar nada
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={carregar} disabled={salvando}>
            <IconAtualizar size={15} /> Recarregar
          </button>
          {podeEditar && (
            <button
              className="btn btn-primary"
              onClick={salvar}
              disabled={!pendentes.length || salvando}
            >
              {salvando
                ? "Salvando…"
                : pendentes.length
                ? `Salvar ${pendentes.length} alteração(ões)`
                : "Nada a salvar"}
            </button>
          )}
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {aviso && (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "#a8e0cd", marginBottom: 14 }}
        >
          <span className="small" style={{ color: "#06694a" }}>
            <IconOk size={13} /> {aviso}
          </span>
        </div>
      )}

      {!podeEditar && (
        <div className="card card-tight" style={{ marginBottom: 14 }}>
          <span className="small muted">
            Você pode ver a configuração, mas não alterá-la. Alterar exige o
            perfil Administrador.
          </span>
        </div>
      )}

      <div className="stack-v">
        {grupos.map((g) => (
          <div className="card" key={g.categoria}>
            <div className="section-title" style={{ marginBottom: 4 }}>{g.titulo}</div>
            <div className="small muted" style={{ marginBottom: 16 }}>{g.descricao}</div>

            <div className="row row-2">
              {g.itens.map((item) => {
                const valor =
                  item.chave in rascunho ? rascunho[item.chave] : item.valor;
                const alterado = item.chave in rascunho;
                const fugiuDoPadrao = String(item.valor) !== String(item.padrao);

                return (
                  <div className="field" key={item.chave}>
                    <label className="label">
                      {item.rotulo}
                      {alterado && (
                        <span
                          className="pill pill-info"
                          style={{ marginLeft: 6, textTransform: "none" }}
                        >
                          não salvo
                        </span>
                      )}
                    </label>

                    {item.tipo === "booleano" ? (
                      <label className="check" style={{ marginTop: 4 }}>
                        <input
                          type="checkbox"
                          checked={Boolean(valor)}
                          disabled={!podeEditar}
                          onChange={(e) => alterar(item.chave, e.target.checked)}
                        />
                        <span>{valor ? "Ligado" : "Desligado"}</span>
                      </label>
                    ) : item.tipo === "escolha" ? (
                      <select
                        value={String(valor)}
                        disabled={!podeEditar}
                        onChange={(e) => alterar(item.chave, e.target.value)}
                      >
                        {item.opcoes.map((o) => (
                          <option key={o} value={o}>{o}</option>
                        ))}
                      </select>
                    ) : item.tipo === "numero" ? (
                      <input
                        type="number"
                        value={valor}
                        min={item.minimo ?? undefined}
                        max={item.maximo ?? undefined}
                        disabled={!podeEditar}
                        onChange={(e) =>
                          alterar(
                            item.chave,
                            e.target.value === "" ? "" : Number(e.target.value)
                          )
                        }
                      />
                    ) : (
                      <input
                        className={item.chave.includes("dir") || item.chave.includes("staging") ? "mono" : ""}
                        value={valor ?? ""}
                        disabled={!podeEditar}
                        onChange={(e) => alterar(item.chave, e.target.value)}
                      />
                    )}

                    {item.ajuda && <div className="field-help">{item.ajuda}</div>}

                    {fugiuDoPadrao && podeEditar && (
                      <button
                        className="btn btn-ghost btn-sm"
                        style={{ padding: "2px 0", marginTop: 2 }}
                        onClick={() => restaurar(item.chave)}
                        title={`Padrão: ${item.padrao}`}
                      >
                        restaurar padrão ({String(item.padrao)})
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>

      <div className="small muted" style={{ marginTop: 16 }}>
        O que <strong>não</strong> fica aqui, por precisar existir antes do banco
        subir: <span className="mono">SECRET_KEY</span>,{" "}
        <span className="mono">POSTGRES_PASSWORD</span> e{" "}
        <span className="mono">PORTA_HTTP</span> continuam no{" "}
        <span className="mono">.env</span>. Credenciais de acesso ficam no cofre,
        nas telas de Servidores e Destinos.
      </div>
    </>
  );
}
