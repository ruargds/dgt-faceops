import React, { useCallback, useEffect, useState } from "react";
import { api, enviarLogo } from "../../api";
import { t } from "../../i18n";
import { usePermissions } from "../../usePermissions";
import { Carregando, Erro } from "../Comuns";
import { IconAtualizar, IconLixeira, IconOk } from "../Icons";

/**
 * A tela se monta a partir do catálogo do backend.
 *
 * Adicionar uma opção nova é uma linha em `config_service.py` — rótulo,
 * tipo, validação e ajuda vêm de lá. Nada aqui precisa mudar. Foi feito
 * assim porque configuração cresce com o projeto, e ter que editar duas
 * pontas para cada campo novo garante que uma delas fica para trás.
 */
const LOGOS = [
  { tipo: "login", rotulo: "Tela de login", dica: "Aparece acima do formulário. Altura de exibição: 40 px.", padrao: "/logos/dgt-login.png" },
  { tipo: "sidebar", rotulo: "Barra lateral", dica: "Fundo escuro — use versão clara ou negativa. Altura: 26 px.", padrao: "/logos/dgt-sidebar.png" },
  { tipo: "favicon", rotulo: "Ícone da aba", dica: "Quadrado, idealmente 64x64.", padrao: "/logos/dgt-favicon.png" },
];

/**
 * Logo por cliente, sem reconstruir a imagem.
 *
 * O arquivo vai para o volume de dados e é servido pelo backend. A mesma
 * imagem Docker atende qualquer cliente — trocar a marca deixa de ser
 * motivo para manter um build por cliente.
 */
function Marca({ onMudou }) {
  const [situacao, setSituacao] = useState({});
  const [ocupado, setOcupado] = useState("");
  const [erro, setErro] = useState("");
  const [versao, setVersao] = useState(Date.now());

  const carregar = useCallback(async () => {
    try {
      setSituacao(await api.marcaSituacao());
    } catch (ex) {
      setErro(ex.message);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function enviar(tipo, arquivo) {
    if (!arquivo) return;
    setOcupado(tipo);
    setErro("");
    try {
      await enviarLogo(tipo, arquivo);
      await carregar();
      setVersao(Date.now());
      onMudou && onMudou();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setOcupado("");
    }
  }

  async function remover(tipo) {
    setOcupado(tipo);
    try {
      await api.removerLogo(tipo);
      await carregar();
      setVersao(Date.now());
      onMudou && onMudou();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setOcupado("");
    }
  }

  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 4 }}>{t("Logotipos")}</div>
      <div className="small muted" style={{ marginBottom: 16 }}>
        Substitui a marca padrão sem reconstruir a aplicação. PNG, JPG, GIF
        ou SVG, até 2 MB. Fica no volume de dados, então sobrevive a
        atualização.
      </div>

      <Erro mensagem={erro} />

      <div className="row row-3">
        {LOGOS.map((l) => {
          const proprio = situacao[l.tipo];
          const src = proprio ? `/api/marca/${l.tipo}?v=${versao}` : l.padrao;
          return (
            <div className="field" key={l.tipo}>
              <label className="label">
                {l.rotulo}
                {proprio && (
                  <span className="pill pill-ok" style={{ marginLeft: 6, textTransform: "none" }}>{t("próprio")}</span>
                )}
              </label>

              <div
                style={{
                  background: l.tipo === "sidebar" ? "var(--navy)" : "var(--bg-2)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius)",
                  padding: 14,
                  marginBottom: 8,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  minHeight: 64,
                }}
              >
                <img
                  src={src}
                  alt={l.rotulo}
                  style={{ maxHeight: 40, maxWidth: "100%" }}
                  onError={(e) => {
                    e.currentTarget.style.display = "none";
                  }}
                />
              </div>

              <div className="stack-h" style={{ gap: 6 }}>
                <label
                  className="btn btn-secondary btn-sm"
                  style={{ cursor: "pointer", margin: 0 }}
                >
                  {ocupado === l.tipo ? "Enviando…" : "Escolher arquivo"}
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/gif,image/svg+xml"
                    style={{ display: "none" }}
                    disabled={ocupado === l.tipo}
                    onChange={(e) => {
                      enviar(l.tipo, e.target.files && e.target.files[0]);
                      e.target.value = "";
                    }}
                  />
                </label>
                {proprio && (
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => remover(l.tipo)}
                    disabled={ocupado === l.tipo}
                    title={t("Voltar ao padrão")}
                  >
                    <IconLixeira size={13} />
                  </button>
                )}
              </div>
              <div className="field-help">{l.dica}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

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
          <div className="page-title">{t("tela.config")}</div>
          <div className="page-sub">
            {t("tela.config.sub")}
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={carregar} disabled={salvando}>
            <IconAtualizar size={15} />{t("Recarregar")}</button>
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
          style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 14 }}
        >
          <span className="small" style={{ color: "var(--green-fg)" }}>
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
          <React.Fragment key={g.categoria}>
          <div className="card">
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
                        >{t("não salvo")}</span>
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
          {g.categoria === "projeto" && podeEditar && (
            <Marca onMudou={() => window.location.reload()} />
          )}
          </React.Fragment>
        ))}
      </div>

      <div className="small muted" style={{ marginTop: 16 }}>{t("O que")}<strong>{t("não")}</strong> fica aqui, por precisar existir antes do banco
        subir: <span className="mono">{t("SECRET_KEY")}</span>,{" "}
        <span className="mono">{t("POSTGRES_PASSWORD")}</span> e{" "}
        <span className="mono">{t("PORTA_HTTP")}</span> continuam no{" "}
        <span className="mono">.env</span>. Credenciais de acesso ficam no cofre,
        nas telas de Servidores e Destinos.
      </div>
    </>
  );
}
