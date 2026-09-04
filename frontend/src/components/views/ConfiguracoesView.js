import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api, enviarLogo } from "../../api";
import { t } from "../../i18n";
import {
  ajudaDeBusca, casaBusca, termosDaBusca,
} from "../../utils/buscaInteligente";
import { usePermissions } from "../../usePermissions";
import { Carregando, Erro, useHosts } from "../Comuns";
import { IconAtualizar, IconLixeira, IconOk } from "../Icons";

// Rótulo amigável para cada chave de limiar — mesmo vocabulário das
// opções globais acima, só que aplicável a um host ou serviço específico.
const ROTULOS_LIMIAR = {
  disco_pct: "Disco acima de (%)",
  mem_pct: "Memória acima de (%)",
  swap_pct: "Swap acima de (%)",
  cpu_pct: "Carga por núcleo acima de (%)",
  gpu_mem_pct: "Memória de vídeo acima de (%)",
  gpu_temp: "Temperatura da GPU acima de (°C)",
  servico_reinicios: "Serviço em loop a partir de (reinícios)",
  servico_indisponivel_min: "Serviço parado vira crítico depois de (minutos)",
};

/**
 * Exceção de limiar por host e/ou serviço.
 *
 * O padrão global fica nas opções "Limiares" acima — aqui é só o que foge
 * dele: "no vm-ftpserver, aceito carga mais alta", "o video-worker pode
 * reiniciar mais vezes antes de virar alarme". Sem exceção nenhuma
 * cadastrada, tudo se comporta exatamente como antes.
 */
function LimiaresPorServico() {
  const { has } = usePermissions();
  const podeEditar = has("users.manage");
  const { hosts } = useHosts(false);

  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [salvando, setSalvando] = useState(false);
  const [novo, setNovo] = useState({ chave: "disco_pct", host_id: "", servico: "", valor: "" });

  const carregar = useCallback(async () => {
    try {
      setDados(await api.limiares());
    } catch (ex) {
      setErro(ex.message);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  const chaveEhServico = dados && dados.chaves_servico.includes(novo.chave);

  async function salvar(e) {
    e.preventDefault();
    if (novo.valor === "") return;
    setSalvando(true);
    setErro("");
    try {
      await api.salvarLimiar({
        chave: novo.chave,
        valor: Number(novo.valor),
        host_id: novo.host_id ? Number(novo.host_id) : null,
        servico: chaveEhServico ? novo.servico.trim() : "",
      });
      setNovo((n) => ({ ...n, servico: "", valor: "" }));
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setSalvando(false);
    }
  }

  async function restaurar(id) {
    try {
      await api.restaurarLimiar(id);
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    }
  }

  if (!dados) return null;

  const nomeHost = (id) => (id ? hosts.find((h) => h.id === id)?.name || `#${id}` : "Todos os hosts");

  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 4 }}>{t("Limiares por servidor ou serviço")}</div>
      <div className="small muted" style={{ marginBottom: 16 }}>
        {t("Exceção ao padrão acima. Sem nada aqui, todo host e todo serviço usa o padrão global. Apagar a exceção volta ao padrão.")}
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {dados.overrides.length > 0 && (
        <div className="table-wrap" style={{ marginBottom: 16 }}>
          <table>
            <thead>
              <tr>
                <th>{t("Onde")}</th>
                <th>{t("Limite")}</th>
                <th>{t("Valor")}</th>
                {podeEditar && <th />}
              </tr>
            </thead>
            <tbody>
              {dados.overrides.map((o) => (
                <tr key={o.id}>
                  <td>
                    {nomeHost(o.host_id)}
                    {o.servico && <span className="mono"> · {o.servico}</span>}
                  </td>
                  <td className="small muted">{ROTULOS_LIMIAR[o.chave] || o.chave}</td>
                  <td>{o.valor}</td>
                  {podeEditar && (
                    <td>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => restaurar(o.id)}
                      >
                        {t("restaurar padrão")}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {podeEditar && (
        <form className="row row-4" onSubmit={salvar} style={{ alignItems: "flex-end" }}>
          <div className="field">
            <label className="label">{t("Limite")}</label>
            <select
              value={novo.chave}
              onChange={(e) => setNovo((n) => ({ ...n, chave: e.target.value, servico: "" }))}
            >
              <optgroup label={t("Da máquina")}>
                {dados.chaves_host.map((c) => (
                  <option key={c} value={c}>{ROTULOS_LIMIAR[c] || c}</option>
                ))}
              </optgroup>
              <optgroup label={t("De um serviço")}>
                {dados.chaves_servico.map((c) => (
                  <option key={c} value={c}>{ROTULOS_LIMIAR[c] || c}</option>
                ))}
              </optgroup>
            </select>
          </div>
          <div className="field">
            <label className="label">{t("Servidor")}</label>
            <select value={novo.host_id} onChange={(e) => setNovo((n) => ({ ...n, host_id: e.target.value }))}>
              <option value="">{t("Todos os hosts")}</option>
              {hosts.map((h) => (
                <option key={h.id} value={h.id}>{h.name}</option>
              ))}
            </select>
          </div>
          {chaveEhServico && (
            <div className="field">
              <label className="label label-required">{t("Serviço")}</label>
              <input
                className="mono"
                placeholder="findface-video-worker"
                value={novo.servico}
                onChange={(e) => setNovo((n) => ({ ...n, servico: e.target.value }))}
                required
              />
            </div>
          )}
          <div className="field">
            <label className="label label-required">{t("Valor")}</label>
            <input
              type="number"
              value={novo.valor}
              onChange={(e) => setNovo((n) => ({ ...n, valor: e.target.value }))}
              required
            />
          </div>
          <div className="field">
            <button className="btn btn-primary" disabled={salvando}>
              {salvando ? t("Salvando…") : t("Adicionar exceção")}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

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

  const [buscaCfg, setBuscaCfg] = useState("");

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

  // ANTES do retorno antecipado, sempre. Hook depois de `if (...) return`
  // é chamado em um render e não no outro, e o React derruba a tela
  // inteira com o erro #310 ("mais hooks que no render anterior") — foi
  // o que quebrou esta tela assim que ela terminava de carregar.
  const gruposFiltrados = useMemo(() => {
    const termos = termosDaBusca(buscaCfg);
    if (termos.length === 0) return grupos;
    return grupos
      .map((g) => ({
        ...g,
        itens: g.itens.filter((i) =>
          casaBusca(termos, i.rotulo, i.chave, i.ajuda, g.titulo),
        ),
      }))
      .filter((g) => g.itens.length > 0);
  }, [grupos, buscaCfg]);

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
            <IconAtualizar size={15} /> {t("Recarregar")}</button>
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

      {/* Sessenta parâmetros em sete categorias. Achar um deles rolando
          a tela é o que a busca evita. Some a categoria que ficou sem
          nenhum item, senão a tela vira uma sequência de títulos vazios. */}
      <div className="filtros" style={{ marginBottom: 14 }}>
        <div className="filtro-busca">
          <input
            type="search"
            value={buscaCfg}
            onChange={(e) => setBuscaCfg(e.target.value)}
            placeholder={t("Buscar configuração…")}
            title={ajudaDeBusca(t("Procura no nome, na chave e na explicação de cada opção."))}
            aria-label={t("Buscar configuração")}
          />
        </div>
        {buscaCfg && (
          <span className="small muted">
            {gruposFiltrados.reduce((n, g) => n + g.itens.length, 0)} {t("opção(ões)")}
          </span>
        )}
      </div>

      <div className="stack-v">
        {gruposFiltrados.length === 0 && buscaCfg && (
          <div className="card card-tight">
            <span className="small muted">{t("Nenhuma configuração bate com a busca.")}</span>
          </div>
        )}
        {gruposFiltrados.map((g) => (
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
          {g.categoria === "alerta" && <LimiaresPorServico />}
          </React.Fragment>
        ))}
      </div>

      <div className="small muted" style={{ marginTop: 16 }}>{t("O que")} <strong>{t("não")}</strong> fica aqui, por precisar existir antes do banco
        subir: <span className="mono">{t("SECRET_KEY")}</span>,{" "}
        <span className="mono">{t("POSTGRES_PASSWORD")}</span> e{" "}
        <span className="mono">{t("PORTA_HTTP")}</span> continuam no{" "}
        <span className="mono">.env</span>. Credenciais de acesso ficam no cofre,
        nas telas de Servidores e Destinos.
      </div>
    </>
  );
}
