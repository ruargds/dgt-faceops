import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes, formatData, formatDuracao } from "../../api";
import { t } from "../../i18n";
import { usePermissions } from "../../usePermissions";
import {
  fecharSeForaLimpo,
  Carregando,
  Erro,
  SeletorDestinos,
  SeletorHost,
  Selo,
  Vazio,
  useDestinos,
  useHosts,
} from "../Comuns";
import { IconAtualizar, IconBackup, IconChave, IconDownload, IconLixeira, IconLogs, IconStop } from "../Icons";

export const PERFIS = [
  {
    id: "config",
    nome: "Config",
    resumo: "configs/ + docker-compose + licença",
    detalhe:
      "Segundos, alguns MB, sem parar nada. Recupera a configuração do sistema, não os dados.",
  },
  {
    id: "essencial",
    nome: "Essencial",
    resumo: "config + PostgreSQL + Tarantool",
    detalhe:
      "Minutos, alguns GB, sem parar nada. Recupera cadastros, usuários, câmeras, dossiês e os vetores faciais. NÃO leva as fotos originais de evento. É o backup do dia a dia.",
  },
  {
    id: "completo",
    nome: "Completo",
    resumo: "procedimento oficial NtechLab — data/ inteiro",
    detalhe:
      "Horas, centenas de GB, PARA o Face Detect durante a cópia. Leva tudo, inclusive as fotos de evento. Use em janela de manutenção.",
  },
];

export default function BackupsView() {
  const { has } = usePermissions();
  const { hosts, erro: erroHosts, carregando: carregandoHosts } = useHosts(false);

  const [lista, setLista] = useState([]);
  const [filtroHost, setFiltroHost] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);
  const [novo, setNovo] = useState(false);
  // Manifesto: o que o artefato contem e o roteiro de restauracao do
  // fabricante, lido de dentro do .tar.gz sem baixar nada.
  const [vendoManifesto, setVendoManifesto] = useState(null);
  const [importando, setImportando] = useState(false);
  const [lote, setLote] = useState(null);
  const [disparandoLote, setDisparandoLote] = useState(false);
  const [verRecuperacao, setVerRecuperacao] = useState(false);
  const [detalhe, setDetalhe] = useState(null);
  const [espaco, setEspaco] = useState(null);
  const [painelRodando, setPainelRodando] = useState(false);

  const carregar = useCallback(async () => {
    setErro("");
    try {
      const params = filtroHost ? `?host_id=${filtroHost}` : "";
      const [runs, disco] = await Promise.all([
        api.backups(params),
        api.armazenamentoPainel().catch(() => null),
      ]);
      setLista(runs);
      if (disco) setEspaco(disco);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, [filtroHost]);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function backupPainel() {
    setPainelRodando(true);
    setErro("");
    try {
      await api.backupDoPainel([]);
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setPainelRodando(false);
    }
  }

  // Enquanto houver execução em andamento, atualiza sozinho a cada 4s.
  // Fora disso não fica pedindo nada — a tela não precisa de polling
  // eterno para mostrar histórico parado.
  const temAndamento = lista.some((r) => r.status === "executando" || r.status === "pendente");
  const refCarregar = useRef(carregar);
  refCarregar.current = carregar;

  useEffect(() => {
    if (!temAndamento) return undefined;
    const id = setInterval(() => refCarregar.current(), 4000);
    return () => clearInterval(id);
  }, [temAndamento]);

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo={t("Cadastre um servidor primeiro")} />;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.backups")}</div>
          <div className="page-sub">
            {t("tela.backups.sub")}
            {espaco &&
              ` · ${formatBytes(espaco.livre_bytes)} livres no disco do painel`}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost
            hosts={hosts}
            hostId={filtroHost}
            onMudar={setFiltroHost}
            incluirTodos
          />
          {has("backups.delete") && (
            <button
              className="btn btn-secondary"
              title="Remove do histórico as execuções que falharam sem gerar artefato"
              onClick={async () => {
                if (
                  !window.confirm(
                    "Remover do histórico todas as execuções que falharam sem " +
                      "gerar artefato?"
                  )
                )
                  return;
                try {
                  const r = await api.limparFalhas();
                  await carregar();
                  window.alert(`${r.removidas} linha(s) removida(s).`);
                } catch (ex) {
                  window.alert(ex.message);
                }
              }}
            >
              Limpar falhas
            </button>
          )}
          <button
            className="btn btn-secondary"
            onClick={() => setVerRecuperacao(true)}
            title="O que dá para recuperar, por servidor"
          >
            Recuperação
          </button>
          <button className="btn btn-secondary" onClick={carregar}>
            <IconAtualizar size={15} /> {t("Atualizar")}</button>
          {has("backups.run") && (
            <>
              {/* Importar: traz artefato de fora para o painel. Nao
                  restaura nada -- e guardar e catalogar, para o backup
                  feito na mao ou vindo de outra instalacao aparecer no
                  historico como qualquer outro. */}
              <label
                className={`btn btn-secondary ${importando ? "disabled" : ""}`}
                style={{ cursor: importando ? "default" : "pointer" }}
                title="Enviar um .tar.gz de backup para o disco do painel"
              >
                {importando ? "Enviando…" : "Importar artefato"}
                <input
                  type="file"
                  accept=".tar.gz,.tgz,.tar"
                  style={{ display: "none" }}
                  disabled={importando}
                  onChange={async (e) => {
                    const arquivo = e.target.files && e.target.files[0];
                    e.target.value = "";
                    if (!arquivo) return;
                    setImportando(true);
                    try {
                      // `filtroHost` e o seletor do cabecalho -- `hostId`
                      // so existe dentro do modal. Usar o nome errado aqui
                      // derrubava a tela no clique, com ReferenceError.
                      await api.importarBackup(arquivo, filtroHost || undefined);
                      await carregar();
                    } catch (ex) {
                      window.alert(ex.message);
                    } finally {
                      setImportando(false);
                    }
                  }}
                />
              </label>
              <button
                className="btn btn-secondary"
                onClick={backupPainel}
                disabled={painelRodando}
                title="Salva o banco do próprio painel: servidores, credenciais cifradas, agendamentos, histórico e auditoria"
              >
                {painelRodando ? "Salvando…" : "Backup do painel"}
              </button>
              <button
                className="btn btn-secondary"
                onClick={async () => {
                  if (
                    !window.confirm(
                      "Disparar backup em TODOS os servidores habilitados?\n\n" +
                        "Cada um recebe o perfil mais completo que suporta " +
                        "(config ou essencial). O perfil completo, que para o " +
                        "Face Detect, nunca entra no lote."
                    )
                  )
                    return;
                  setDisparandoLote(true);
                  try {
                    setLote(await api.backupTodos({ perfil: "auto", destinos: [] }));
                    await carregar();
                  } catch (ex) {
                    window.alert(ex.message);
                  } finally {
                    setDisparandoLote(false);
                  }
                }}
                disabled={disparandoLote}
                title="Um backup por servidor, com o perfil que cada um suporta"
              >
                {disparandoLote ? "Disparando…" : "Todos os servidores"}
              </button>
              <button className="btn btn-primary" onClick={() => setNovo(true)}>
                <IconBackup size={15} /> {t("Novo backup")}</button>
            </>
          )}
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {!carregando && !lista.some((r) => r.profile === "painel" && r.status === "sucesso") && (
        <div
          className="card card-tight"
          style={{ background: "var(--amber-bg)", borderColor: "var(--amber-bd)", marginBottom: 14 }}
        >
          <span className="small" style={{ color: "var(--amber-fg)" }}>
            <strong>{t("O painel nunca foi salvo.")}</strong> Se esta máquina morrer,
            perdem-se o cadastro dos servidores, as credenciais cifradas, os
            agendamentos, o histórico e a auditoria. São alguns MB — use o
            botão <strong>{t("Backup do painel")}</strong>.
          </span>
        </div>
      )}

      {lote && (
        <div className="card" style={{ marginBottom: 14 }}>
          <div className="stack-h" style={{ justifyContent: "space-between" }}>
            <div className="section-title" style={{ marginBottom: 0 }}>
              Backup em lote
            </div>
            <button className="btn btn-ghost btn-sm" onClick={() => setLote(null)}>
              {t("Fechar")}
            </button>
          </div>
          {lote.disparados.length > 0 && (
            <div className="small" style={{ marginTop: 8 }}>
              <strong>Disparados:</strong>{" "}
              {lote.disparados.map((d) => `${d.host} (${d.perfil})`).join(" · ")}
            </div>
          )}
          {lote.pulados.length > 0 && (
            <div className="small" style={{ marginTop: 6, color: "var(--amber-fg)" }}>
              <strong>Pulados:</strong>{" "}
              {lote.pulados.map((p) => `${p.host} — ${p.motivo}`).join(" · ")}
            </div>
          )}
          <div className="small muted" style={{ marginTop: 6 }}>
            Cada artefato vai para a pasta do seu servidor no destino: o lote não
            mistura nada, só evita a ida manual em cada tela.
          </div>
        </div>
      )}

      {verRecuperacao && <ModalRecuperacao onFechar={() => setVerRecuperacao(false)} />}

      {carregando ? (
        <Carregando />
      ) : lista.length === 0 ? (
        <Vazio titulo={t("Nenhum backup ainda")}>{t("Dispare um backup")} <strong>{t("Essencial")}</strong> para validar o caminho de
          ponta a ponta antes de programar a recorrência.
        </Vazio>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("Quando")}</th>
                <th>{t("Servidor")}</th>
                <th>{t("Perfil")}</th>
                <th>{t("Situação")}</th>
                <th className="right">{t("Tamanho")}</th>
                <th>{t("Destinos")}</th>
                <th>{t("Disparado por")}</th>
                <th style={{ width: 1 }}></th>
              </tr>
            </thead>
            <tbody>
              {lista.map((r) => (
                <LinhaBackup
                  onManifesto={setVendoManifesto}
                  key={r.id}
                  r={r}
                  onDetalhe={() => setDetalhe(r.id)}
                  onRemover={carregar}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {vendoManifesto && (
        <ModalManifesto
          run={vendoManifesto}
          onFechar={() => setVendoManifesto(null)}
        />
      )}

      {novo && (
        <ModalNovoBackup
          hosts={hosts.filter((h) => h.enabled)}
          onFechar={() => setNovo(false)}
          onPronto={async () => {
            setNovo(false);
            await carregar();
          }}
        />
      )}

      {detalhe && <ModalDetalhe runId={detalhe} onFechar={() => setDetalhe(null)} />}
    </>
  );
}

/**
 * Volume estimado e espaço disponível, antes de disparar.
 *
 * A pergunta certa antes de um backup é "cabe?" — e ela vinha sendo
 * respondida descobrindo. Aqui ela é respondida antes, com duas fontes: a
 * medição no servidor (`configs/`, bancos, diretório de dados) e o
 * **tamanho real das execuções anteriores** daquele perfil naquele
 * servidor. Número observado ganha de fator de compressão estimado.
 *
 * Onde a medição não terminou — `du` numa árvore com milhões de fotos de
 * evento leva minutos —, a tela diz "não medido" em vez de mostrar um
 * número pequeno que faria alguém disparar achando que cabe.
 */
function Estimativa({ dados, perfil }) {
  const m = dados.medicao || {};
  const escolhido = (dados.perfis || []).find((p) => p.perfil === perfil);
  const estimado = escolhido && escolhido.estimado_bytes;
  const livre = dados.livre_no_painel;
  const naoCabe = estimado && livre && estimado > livre * 0.9;

  const linha = (rotulo, valor, ajuda) => (
    <tr key={rotulo}>
      <td title={ajuda}>{rotulo}</td>
      <td className="right mono">
        {valor === null || valor === undefined ? "não medido" : formatBytes(valor)}
      </td>
    </tr>
  );

  return (
    <>
      <div className="table-wrap">
        <table className="tabela-densa">
          <tbody>
            {linha("configs/", m.configs_bytes, "Configuração da instalação")}
            {linha(
              "Bancos (PostgreSQL/Timescale)",
              m.bancos_bytes,
              "Soma do tamanho dos bancos, perguntado ao próprio PostgreSQL"
            )}
            {linha("Tarantool (vetores)", m.tarantool_bytes, "Vetores faciais")}
            {linha(
              "Diretório de dados",
              m.data_bytes,
              "Inclui as fotos de evento — é o que pesa no perfil completo"
            )}
          </tbody>
        </table>
      </div>

      <div className="table-wrap" style={{ marginTop: 8 }}>
        <table className="tabela-densa">
          <thead>
            <tr>
              <th>Artefato estimado</th>
              <th className="right">Tamanho</th>
              <th>De onde vem o número</th>
            </tr>
          </thead>
          <tbody>
            {(dados.perfis || []).map((p) => (
              <tr
                key={p.perfil}
                style={p.perfil === perfil ? { fontWeight: 600 } : undefined}
              >
                <td>{p.perfil}</td>
                <td className="right mono">
                  {p.estimado_bytes ? formatBytes(p.estimado_bytes) : "não medido"}
                </td>
                <td className="small muted">
                  {p.estimado_bytes ? p.origem : p.observacao}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="small muted" style={{ marginTop: 8 }}>
        Livre no disco do painel: <strong>{formatBytes(livre || 0)}</strong>
        {m.livre_no_staging
          ? ` · livre no staging do servidor: ${formatBytes(m.livre_no_staging)}`
          : ""}
        . O artefato é montado no servidor e copiado para cá — precisa caber
        nos dois lados.
      </div>

      {naoCabe && (
        <div
          className="card card-tight"
          style={{
            background: "var(--red-bg)",
            borderColor: "var(--red-bd)",
            marginTop: 8,
          }}
        >
          <span className="small" style={{ color: "var(--red-fg)" }}>
            O artefato estimado ocupa mais de 90% do que resta no disco do
            painel. Libere espaço (Manutenção → limpeza pontual, ou a retenção
            dos artefatos) antes de disparar.
          </span>
        </div>
      )}
    </>
  );
}

/**
 * Roteiro de restauração.
 *
 * O manual explica o procedimento e o manifesto diz o que o artefato tem.
 * Nenhum dos dois responde a pergunta do incidente: **quais comandos eu
 * digito, nesta máquina?**
 *
 * Aqui os passos saem prontos, com o caminho real da instalação daquele
 * servidor (que neste ambiente é `/media/STORAGE/findface-multi`, e não o
 * `/opt` do manual), o nome do projeto compose e só o que o artefato
 * realmente traz — sem passo de Tarantool num backup que não tem Tarantool.
 *
 * O painel monta; quem digita é gente. Restore sobrescreve produção.
 */
function Roteiro({ dados }) {
  const [copiado, setCopiado] = useState(null);

  async function copiar(texto, n) {
    try {
      await navigator.clipboard.writeText(texto);
      setCopiado(n);
      setTimeout(() => setCopiado(null), 1500);
    } catch {
      /* navegador sem permissão — o texto continua selecionável */
    }
  }

  return (
    <>
      <div
        className="card card-tight"
        style={{ background: "var(--amber-bg)", borderColor: "var(--amber-bd)", marginBottom: 12 }}
      >
        <span className="small" style={{ color: "var(--amber-fg)" }}>
          <strong>Antes de tudo:</strong> {dados.aviso_versao} Este artefato foi
          feito em <span className="mono">{dados.feito_em || "—"}</span>, no
          servidor <span className="mono">{dados.feito_no_servidor || dados.servidor}</span>.
        </span>
      </div>

      <div className="grid-stats" style={{ marginBottom: 12 }}>
        <div className="card card-tight stat" title="Onde o Face Detect está instalado NESTE servidor">
          <span className="stat-label">Instalação</span>
          <div className="mono small">{dados.ff_dir}</div>
        </div>
        <div className="card card-tight stat">
          <span className="stat-label">Projeto compose</span>
          <div className="mono small">{dados.projeto}</div>
        </div>
        <div className="card card-tight stat">
          <span className="stat-label">Servidor de destino</span>
          <div className="mono small">
            {dados.servidor} · {dados.endereco}
          </div>
        </div>
        <div className="card card-tight stat">
          <span className="stat-label">Perfil</span>
          <div className="stat-value">{dados.perfil}</div>
        </div>
      </div>

      <div className="stack-v" style={{ gap: 10 }}>
        {dados.passos.map((p) => (
          <div className="card card-tight" key={p.n}>
            <div className="stack-h" style={{ justifyContent: "space-between" }}>
              <div style={{ fontWeight: 600 }}>
                {p.n}. {p.titulo}
              </div>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => copiar(p.comando, p.n)}
              >
                {copiado === p.n ? "copiado" : "copiar"}
              </button>
            </div>
            <pre
              className="mono small"
              style={{
                whiteSpace: "pre-wrap",
                background: "var(--term-bg)",
                color: "#FFFFFF",
                padding: "8px 10px",
                borderRadius: "var(--radius)",
                marginTop: 6,
                overflowX: "auto",
              }}
            >
              {p.comando}
            </pre>
            <div className="small muted" style={{ marginTop: 6 }}>
              {p.porque}
            </div>
            {p.cuidado && (
              <div className="small" style={{ color: "var(--red-fg)", marginTop: 4 }}>
                <strong>Cuidado:</strong> {p.cuidado}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="small muted" style={{ marginTop: 10 }}>
        O painel monta o roteiro; a execução é sua, no servidor. Nada aqui roda
        sozinho — restore sobrescreve produção. O procedimento completo, com o
        que fazer quando só existe backup essencial, está em{" "}
        <span className="mono">docs/03_RESTORE.md</span>.
      </div>
    </>
  );
}

/**
 * Plano de recuperação.
 *
 * A pergunta que só aparece no pior dia: *"se eu precisar voltar agora,
 * tenho o quê, de quando, e o que falta?"*. Responder isso lendo o
 * histórico linha a linha, no meio de um incidente, é o pior momento
 * possível para descobrir que um servidor não tinha backup nenhum.
 *
 * Por servidor, porque o ambiente é distribuído: cada máquina guarda um
 * pedaço diferente, e cada artefato volta na máquina de onde saiu.
 *
 * Não executa restore. O procedimento continua manual, com o roteiro do
 * fabricante dentro do manifesto de cada artefato.
 */
function ModalRecuperacao({ onFechar }) {
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");

  useEffect(() => {
    let vivo = true;
    api
      .recuperacao()
      .then((r) => vivo && setDados(r))
      .catch((ex) => vivo && setErro(ex.message));
    return () => {
      vivo = false;
    };
  }, []);

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="modal-title">O que dá para recuperar</div>
          <button className="btn btn-ghost btn-sm" onClick={onFechar}>
            {t("Fechar")}
          </button>
        </div>
        <div className="modal-body">
          <Erro mensagem={erro} />
          {!dados && !erro && <Carregando />}

          {dados && dados.sem_backup.length > 0 && (
            <div
              className="card card-tight"
              style={{
                background: "var(--red-bg)",
                borderColor: "var(--red-bd)",
                marginBottom: 12,
              }}
            >
              <span className="small" style={{ color: "var(--red-fg)" }}>
                Sem backup disponível: <strong>{dados.sem_backup.join(", ")}</strong>.
                Se um deles cair agora, não há de onde voltar.
              </span>
            </div>
          )}

          {dados &&
            dados.servidores.map((s) => (
              <div
                className="card card-tight"
                key={s.servidor}
                style={{ marginBottom: 10 }}
              >
                <div style={{ fontWeight: 600, color: "var(--titulo)" }}>
                  {s.servidor}
                </div>
                {Object.keys(s.perfis).length === 0 ? (
                  <div
                    className="small"
                    style={{ color: "var(--amber-fg)", marginTop: 4 }}
                  >
                    {s.aviso || "nenhum backup disponível"}
                  </div>
                ) : (
                  <div className="table-wrap" style={{ marginTop: 8 }}>
                    <table className="tabela-densa">
                      <thead>
                        <tr>
                          <th>Perfil</th>
                          <th>De quando</th>
                          <th>Recupera</th>
                          <th>Não recupera</th>
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(s.perfis).map(([perfil, info]) => (
                          <tr key={perfil}>
                            <td>
                              <span className="pill pill-idle">{perfil}</span>
                              <div className="small muted mono">{info.artefato}</div>
                            </td>
                            <td
                              className="small"
                              style={
                                info.idade.dias !== null && info.idade.dias > 7
                                  ? { color: "var(--amber-fg)" }
                                  : undefined
                              }
                            >
                              {info.idade.texto}
                            </td>
                            <td className="small">{info.recupera}</td>
                            <td className="small muted">{info.nao_recupera}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))}

          {dados && (
            <div className="small muted" style={{ marginTop: 10 }}>
              {dados.observacao}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * O manifesto do artefato.
 *
 * Vive dentro do .tar.gz e traz três coisas que decidem uma restauração: o
 * que o backup contém, **a versão das imagens do Face Detect** e o roteiro
 * oficial do fabricante. A versão importa mais do que parece — a base do
 * Tarantool não é compatível entre versões maiores, e restaurar num sistema
 * de outra versão devolve os cadastros e não devolve o reconhecimento.
 *
 * Ler aqui evita baixar dezenas de GB só para conferir o que veio dentro.
 */
function ModalManifesto({ run, onFechar }) {
  const [texto, setTexto] = useState("");
  const [erro, setErro] = useState("");
  const [lendo, setLendo] = useState(true);
  // Duas leituras da mesma coisa: o manifesto cru e o roteiro montado a
  // partir dele. Quem já conhece o procedimento quer o cru; quem está no
  // meio de um incidente quer os comandos prontos.
  const [aba, setAba] = useState("roteiro");
  const [roteiro, setRoteiro] = useState(null);
  const [erroRoteiro, setErroRoteiro] = useState("");

  useEffect(() => {
    let vivo = true;
    api
      .manifesto(run.id)
      .then((r) => vivo && setTexto(r.manifesto || ""))
      .catch((ex) => vivo && setErro(ex.message))
      .finally(() => vivo && setLendo(false));
    api
      .roteiro(run.id)
      .then((r) => vivo && setRoteiro(r))
      .catch((ex) => vivo && setErroRoteiro(ex.message));
    return () => {
      vivo = false;
    };
  }, [run.id]);

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="modal-title">
            Manifesto — {run.artifact_name || `execução #${run.id}`}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onFechar}>
            {t("Fechar")}
          </button>
        </div>
        <div className="modal-body">
          <div className="stack-h" style={{ gap: 6, marginBottom: 10 }}>
            <button
              type="button"
              className={`btn btn-sm ${aba === "roteiro" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setAba("roteiro")}
            >
              Roteiro de restauração
            </button>
            <button
              type="button"
              className={`btn btn-sm ${aba === "manifesto" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setAba("manifesto")}
            >
              Manifesto
            </button>
          </div>

          {aba === "manifesto" && (
            <>
              {lendo && <Carregando texto="Abrindo o artefato no disco do painel…" />}
              <Erro mensagem={erro} />
              {texto && (
                <pre
                  className="mono small"
                  style={{ whiteSpace: "pre-wrap", maxHeight: "60vh", overflow: "auto" }}
                >
                  {texto}
                </pre>
              )}
            </>
          )}

          {aba === "roteiro" && (
            <>
              <Erro mensagem={erroRoteiro} />
              {!roteiro && !erroRoteiro && <Carregando texto="Montando o roteiro…" />}
              {roteiro && <Roteiro dados={roteiro} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function LinhaBackup({ r, onDetalhe, onRemover, onManifesto }) {
  const { has } = usePermissions();
  const [removendo, setRemovendo] = useState(false);
  const emAndamento = r.status === "executando" || r.status === "pendente";

  async function remover() {
    // Sem artefato (falha, ou linha já expirada) não há arquivo a apagar:
    // o que sai é o registro. Dizer isso na pergunta evita a dúvida de
    // "vou perder o backup?" na hora de limpar erro antigo.
    const so_registro = !r.artifact_name || r.expired;
    const pergunta = so_registro
      ? "Remover esta execução do histórico? O arquivo já não está disponível."
      : `Apagar o artefato ${r.artifact_name} em todos os destinos e remover do histórico?`;
    if (!window.confirm(pergunta)) return;
    setRemovendo(true);
    try {
      const resposta = await api.removerBackup(r.id);
      await onRemover();
      // Destino que recusou apagar precisa aparecer: "apaguei" que deixou
      // copia no Azure e nao avisou e a pior forma de nao apagar.
      if (resposta && resposta.sobrou && resposta.sobrou.length > 0) {
        window.alert(
          "A linha saiu do histórico, mas o arquivo continua em: " +
            resposta.sobrou
              .map((s) => `${s.destino} (${s.erro || "motivo não informado"})`)
              .join("; ")
        );
      }
    } catch (ex) {
      window.alert(ex.message);
    } finally {
      setRemovendo(false);
    }
  }

  const duracao =
    r.finished_at && r.started_at
      ? (new Date(r.finished_at) - new Date(r.started_at)) / 1000
      : null;

  return (
    <tr>
      <td className="small">{formatData(r.started_at)}</td>
      <td className="mono small">{r.host_nome}</td>
      <td>
        <span className="pill pill-idle">{r.profile}</span>
        {r.caused_downtime && r.downtime_seconds > 0 && (
          <div className="small" style={{ color: "var(--amber)", marginTop: 3 }}>
            parou {formatDuracao(r.downtime_seconds)}
          </div>
        )}
      </td>
      <td style={{ minWidth: 190 }}>
        <Selo status={r.status} />
        {emAndamento && (
          <div style={{ marginTop: 5 }}>
            <div className="small muted" style={{ marginBottom: 3 }}>
              {r.stage} · {r.progress}%
            </div>
            <div className="progress">
              <div className="progress-fill" style={{ width: `${r.progress}%` }} />
            </div>
          </div>
        )}
        {r.status === "falha" && r.error && (
          <div className="small" style={{ color: "var(--red)", marginTop: 3, maxWidth: 280 }}>
            {r.error.slice(0, 130)}
          </div>
        )}
        {duracao != null && !emAndamento && (
          <div className="small muted" style={{ marginTop: 2 }}>
            levou {formatDuracao(duracao)}
          </div>
        )}
      </td>
      <td className="right mono small">{r.size_bytes ? formatBytes(r.size_bytes) : "—"}</td>
      <td>
        <div className="stack-h" style={{ gap: 4 }}>
          {(r.destinations || []).map((d, i) => (
            <span
              key={i}
              className={`pill ${d.status === "ok" ? "pill-ok" : "pill-err"}`}
              title={d.error || d.uri}
            >
              {d.nome || d.type}
            </span>
          ))}
          {(!r.destinations || r.destinations.length === 0) && (
            <span className="muted small">—</span>
          )}
        </div>
      </td>
      <td className="small muted">{r.triggered_by}</td>
      <td>
        <div className="stack-h" style={{ gap: 6, flexWrap: "nowrap" }}>
          <button className="btn btn-secondary btn-sm" onClick={onDetalhe} title={t("Ver log")}>
            <IconLogs size={14} />
          </button>
          {/* Parar: só faz sentido enquanto roda. Cancelamento não é
              falha — é decisão de quem opera, e o histórico registra assim. */}
          {(r.status === "executando" || r.status === "pendente") && (
            <button
              type="button"
              className="btn btn-danger btn-sm"
              title="Parar esta execução"
              onClick={async () => {
                if (!window.confirm("Parar esta execução de backup?")) return;
                try {
                  await api.cancelarBackup(r.id);
                  onRemover && onRemover();
                } catch (ex) {
                  window.alert(ex.message);
                }
              }}
            >
              <IconStop size={14} />
            </button>
          )}

          {/* Manifesto: existe sempre que o artefato existe, e é o que se
              lê ANTES de decidir restaurar. */}
          {r.artifact_name && !r.expired && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => onManifesto && onManifesto(r)}
              title="Ver o conteúdo e o roteiro de restauração"
            >
              <IconChave size={14} />
            </button>
          )}
          {has("backups.download") && r.artifact_name && !r.expired && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => api.baixarBackup(r.id).catch((e) => window.alert(e.message))}
              title={t("Baixar artefato")}
            >
              <IconDownload size={14} />
            </button>
          )}
          {/* Sempre que se pode apagar, o botão aparece — inclusive em
              linha já marcada como expirada. Esconder o botão ali criava o
              pior estado possível: um registro que não dá para usar nem
              para remover, encalhado na tela para sempre. */}
          {has("backups.delete") && (
            <button
              className="btn btn-danger btn-sm"
              onClick={remover}
              disabled={removendo}
              title={t("Apagar artefato")}
            >
              <IconLixeira size={14} />
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

function ModalNovoBackup({ hosts, onFechar, onPronto }) {
  // Perfis vem do servidor escolhido: um FTP server nao tem o que copiar
  // no perfil essencial, e oferecer assim mesmo garante uma falha em 0s
  // que ninguem devia ter esperado.
  const [perfisHost, setPerfisHost] = useState(null);
  // Estimativa de volume: uma leitura no servidor, disparada junto com a
  // consulta de perfis. Chega depois -- `du` leva alguns segundos -- e a
  // tela não espera por ela para deixar escolher.
  const [estimativa, setEstimativa] = useState(null);
  const [medindo, setMedindo] = useState(false);
  // Erro da medicao aparece na tela. Engolir a falha aqui repetiria o erro
  // que ja custou caro nesta tela: a estimativa simplesmente nao apareceria,
  // e ninguem saberia se e porque nao mediu ou porque a tela nao faz isso.
  const [erroEstimativa, setErroEstimativa] = useState("");
  const { ativos, padroes, carregando: carregandoDest } = useDestinos();
  const [hostId, setHostId] = useState(hosts[0] ? hosts[0].id : null);
  const [perfil, setPerfil] = useState("essencial");
  const [destinos, setDestinos] = useState([]);
  const [tocou, setTocou] = useState(false);
  const [aceito, setAceito] = useState(false);
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  const todos = hostId === "todos";
  const host = hosts.find((h) => h.id === hostId);

  useEffect(() => {
    // Em "todos" nao ha um host unico para consultar -- e sondar os quatro
    // so para desenhar o formulario seria SSH de graca em producao.
    if (!hostId || hostId === "todos") return undefined;
    let vivo = true;
    setPerfisHost(null);
    api
      .perfisDoHost(hostId)
      .then((r) => vivo && setPerfisHost(r))
      .catch(() => {});

    setEstimativa(null);
    setErroEstimativa("");
    setMedindo(true);
    api
      .estimativa(hostId)
      .then((r) => vivo && setEstimativa(r))
      .catch((ex) => vivo && setErroEstimativa(ex.message))
      .finally(() => vivo && setMedindo(false));

    return () => {
      vivo = false;
    };
  }, [hostId]);

  const infoPerfil = (id) =>
    perfisHost && perfisHost.perfis
      ? perfisHost.perfis.find((p) => p.id === id)
      : null;

  // Pre-seleciona os destinos padrao, mas so ate o operador mexer —
  // depois disso a escolha dele manda.
  useEffect(() => {
    if (!tocou && padroes.length) setDestinos(padroes);
  }, [padroes, tocou]);

  async function enviar(e) {
    e.preventDefault();
    if (!destinos.length) {
      setErro("Escolha pelo menos um destino.");
      return;
    }
    setErro("");
    setEnviando(true);
    try {
      if (todos) {
        await api.backupTodos({ perfil: "auto", destinos });
        await onPronto();
        return;
      }
      await api.dispararBackup(hostId, {
        perfil,
        destinos,
        aceito_downtime: aceito,
      });
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
          <div className="modal-title">{t("Novo backup")}</div>
        </div>
        <div className="modal-body">
          {erro && <div className="login-err">{erro}</div>}

          <div className="field">
            <label className="label label-required">{t("Servidor")}</label>
            <select
              value={hostId ?? ""}
              onChange={(e) =>
                setHostId(
                  e.target.value === "todos" ? "todos" : Number(e.target.value)
                )
              }
            >
              {/* "Todos" mora aqui, e nao num botao separado: o lugar de
                  escolher em quantos servidores rodar e o mesmo de escolher
                  em qual. */}
              <option value="todos">Todos os servidores</option>
              {hosts.map((h) => (
                <option key={h.id} value={h.id}>{h.name}</option>
              ))}
            </select>
          </div>

          {todos && (
            <div className="field">
              <label className="label label-required">{t("Perfil")}</label>
              <div
                className="card card-tight"
                style={{ background: "var(--bg-2)", marginBottom: 8 }}
              >
                <div className="small">
                  <strong>Cada servidor recebe o que a função dele permite</strong>,
                  decidido na hora: quem tem banco do Face Detect leva{" "}
                  <strong>essencial</strong>; quem só tem a instalação leva{" "}
                  <strong>config</strong>; quem não hospeda o Face Detect é{" "}
                  <strong>pulado com o motivo</strong>, em vez de falhar em 0s.
                </div>
                <div className="small muted" style={{ marginTop: 6 }}>
                  Cada artefato vai para a pasta do próprio servidor no destino —
                  o lote não mistura nada. O perfil <strong>completo</strong> não
                  entra aqui: ele PARA o Face Detect, e parar todos os servidores de
                  uma vez tem de ser decisão sua, servidor a servidor, com o
                  aceite da janela.
                </div>
              </div>
            </div>
          )}

          {!todos && perfisHost && perfisHost.aviso && (
            <div
              className="card card-tight"
              style={{ background: "var(--amber-bg)", borderColor: "var(--amber-bd)", marginBottom: 12 }}
            >
              <span className="small" style={{ color: "var(--amber-fg)" }}>
                {perfisHost.aviso}
              </span>
            </div>
          )}

          {!todos && (
          <div className="field">
            <label className="label label-required">{t("Perfil")}</label>
            <div className="stack-v" style={{ gap: 8 }}>
              {PERFIS.map((p) => {
                const info = infoPerfil(p.id);
                // Enquanto a consulta nao volta, tudo segue habilitado: a
                // tela nao pode travar por causa de uma leitura pendente.
                const bloqueado = Boolean(info && info.disponivel === false);
                return (
                <label
                  key={p.id}
                  className="card card-tight"
                  title={info ? info.motivo : ""}
                  style={{
                    cursor: bloqueado ? "not-allowed" : "pointer",
                    opacity: bloqueado ? 0.55 : 1,
                    borderColor: perfil === p.id ? "var(--blue)" : "var(--border)",
                    boxShadow: perfil === p.id ? "0 0 0 3px rgba(26,111,196,.10)" : "none",
                  }}
                >
                  <div className="stack-h" style={{ alignItems: "flex-start", gap: 10 }}>
                    <input
                      type="radio"
                      checked={perfil === p.id}
                      disabled={bloqueado}
                      onChange={() => setPerfil(p.id)}
                      style={{ width: "auto", marginTop: 3 }}
                    />
                    <div>
                      <div style={{ fontWeight: 600 }}>
                        {p.nome}{" "}
                        <span className="small muted" style={{ fontWeight: 400 }}>
                          — {p.resumo}
                        </span>
                        {info && info.disponivel === false && (
                          <div className="small" style={{ color: "var(--amber-fg)", fontWeight: 400 }}>
                            indisponível aqui: {info.motivo}
                          </div>
                        )}
                      </div>
                      <div className="small muted" style={{ marginTop: 3 }}>{p.detalhe}</div>
                    </div>
                  </div>
                </label>
                );
              })}
            </div>
          </div>
          )}

          {!todos && (medindo || estimativa || erroEstimativa) && (
            <div className="field">
              <label className="label">Volume e espaço</label>
              {medindo && !estimativa && (
                <div className="small muted">
                  Medindo no servidor: configs/, bancos e diretório de dados. O
                  diretório de dados pode levar alguns segundos.
                </div>
              )}
              {erroEstimativa && (
                <div className="small" style={{ color: "var(--amber-fg)" }}>
                  Não consegui medir: {erroEstimativa}. Dá para disparar assim
                  mesmo — a estimativa é uma conferência, não um requisito.
                </div>
              )}
              {estimativa && <Estimativa dados={estimativa} perfil={perfil} />}
            </div>
          )}

          <div className="field">
            <label className="label label-required">{t("Destinos")}</label>
            {carregandoDest ? (
              <Carregando texto={t("Carregando destinos…")} />
            ) : (
              <SeletorDestinos
                destinos={ativos}
                selecionados={destinos}
                onMudar={(v) => {
                  setTocou(true);
                  setDestinos(v);
                }}
              />
            )}
            <div className="field-help">{t("Cadastre e teste destinos em")} <strong>{t("Destinos")}</strong>.
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
                <span style={{ color: "var(--red-fg)" }}>{t("Entendo que o perfil Completo")} <strong>{t("PARA o Face Detect")}</strong> em{" "}
                  <strong>{host ? host.name : "este servidor"}</strong> durante a cópia
                  (pode levar horas) e que o reconhecimento facial fica fora do ar nesse
                  período.
                </span>
              </label>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>{t("Cancelar")}</button>
          <button
            className="btn btn-primary"
            disabled={enviando || (!todos && perfil === "completo" && !aceito)}
          >
            {enviando
              ? "Disparando…"
              : todos
              ? "Disparar em todos"
              : "Disparar backup"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ModalDetalhe({ runId, onFechar }) {
  const [run, setRun] = useState(null);
  const [erro, setErro] = useState("");

  useEffect(() => {
    let vivo = true;
    const buscar = () =>
      api
        .backup(runId)
        .then((r) => vivo && setRun(r))
        .catch((e) => vivo && setErro(e.message));

    buscar();
    // Acompanha ao vivo enquanto estiver rodando
    const id = setInterval(() => {
      if (vivo) buscar();
    }, 3000);
    return () => {
      vivo = false;
      clearInterval(id);
    };
  }, [runId]);

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <div className="modal modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="modal-title">
            Execução #{runId} {run && `— ${run.host_nome} / ${run.profile}`}
          </div>
          <button className="btn btn-ghost btn-sm" onClick={onFechar}>{t("Fechar")}</button>
        </div>
        <div className="modal-body">
          <Erro mensagem={erro} />
          {!run ? (
            <Carregando />
          ) : (
            <div className="stack-v">
              <div className="grid-stats">
                <div className="card card-tight stat">
                  <span className="stat-label">{t("Situação")}</span>
                  <div><Selo status={run.status} /></div>
                  <span className="stat-sub">{run.stage}</span>
                </div>
                <div className="card card-tight stat">
                  <span className="stat-label">{t("Artefato")}</span>
                  <div className="mono small">{run.artifact_name || "—"}</div>
                  <span className="stat-sub">{formatBytes(run.size_bytes)}</span>
                </div>
                <div className="card card-tight stat">
                  <span className="stat-label">{t("Checksum SHA-256")}</span>
                  <div className="mono small" style={{ wordBreak: "break-all" }}>
                    {run.checksum_sha256 ? run.checksum_sha256.slice(0, 32) + "…" : "—"}
                  </div>
                </div>
              </div>

              {run.error && (
                <div className="card card-tight" style={{ background: "var(--red-bg)", borderColor: "var(--red-bd)" }}>
                  <div className="small" style={{ color: "var(--red-fg)" }}>{run.error}</div>
                </div>
              )}

              <div>
                <div className="section-title">{t("Log da execução")}</div>
                <div className="log">{run.log || "(sem log ainda)"}</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
