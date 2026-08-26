import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes, nivel } from "../../api";
import { usePermissions } from "../../usePermissions";
import {
  Carregando,
  ConfirmarDigitando,
  Erro,
  Medidor,
  SeletorHost,
  Vazio,
  useHosts,
} from "../Comuns";
import { IconAlerta, IconAtualizar, IconLogs, IconOk } from "../Icons";

/**
 * Manutenção de disco e log.
 *
 * Existe porque o problema mais comum num servidor de reconhecimento
 * facial não é o FindFace — é o disco raiz enchendo de log. E resolver
 * isso não deveria exigir linha de comando em quatro máquinas diferentes.
 */
/**
 * Limpeza de eventos antigos — procedimento oficial da NtechLab.
 *
 * É o que ataca a causa do disco cheio: num servidor real as fotos de
 * evento ocupavam 242 GB de 268 GB. Backup não resolve isso; retenção de
 * evento resolve.
 *
 * A idade vai em dias na tela e vira segundos no comando. Pedir segundos
 * ao operador seria convite a apagar cinco anos achando que apagou cinco
 * dias.
 */
function Limpeza({ hostId, hostNome }) {
  const { has } = usePermissions();
  const [dados, setDados] = useState(null);
  const [selecao, setSelecao] = useState({});
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [confirmando, setConfirmando] = useState(false);
  const [resultado, setResultado] = useState(null);

  useEffect(() => {
    setDados(null);
    setSelecao({});
    setResultado(null);
  }, [hostId]);

  async function carregar() {
    setCarregando(true);
    setErro("");
    try {
      setDados(await api.limpezaOpcoes(hostId));
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }

  const itens = Object.entries(selecao)
    .filter(([, v]) => v.marcado)
    .map(([opcao, v]) => ({ opcao, dias: Number(v.dias) }));

  const temZero = itens.some((i) => i.dias === 0);

  return (
    <div className="card">
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 4 }}>
        <div className="section-title" style={{ marginBottom: 0 }}>
          Limpeza de eventos antigos
        </div>
        <button className="btn btn-secondary btn-sm" onClick={carregar} disabled={carregando}>
          {carregando ? "Consultando…" : dados ? "Recarregar" : "Consultar opções"}
        </button>
      </div>
      <div className="small muted" style={{ marginBottom: 14 }}>
        Procedimento oficial da NtechLab. É o que libera espaço de verdade —
        as fotos de evento são quase todo o volume do diretório de dados.
        <strong> Irreversível: não há lixeira.</strong>
      </div>

      <Erro mensagem={erro} />

      {dados && dados.em_andamento && (
        <div
          className="card card-tight"
          style={{ background: "var(--amber-bg)", borderColor: "#f5d9a8", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "#8a4b00" }}>
            Há uma limpeza em andamento neste servidor. Enquanto ela roda, o
            painel recusa reiniciar container e parar o stack — o manual é
            explícito de que isso corromperia o banco.
          </span>
        </div>
      )}

      {dados && !dados.confirmado_pelo_servidor && (
        <div className="small" style={{ color: "var(--amber)", marginBottom: 10 }}>
          O servidor não respondeu ao <span className="mono">--help</span>. A
          lista abaixo é a documentada para a 2.4.1 e pode não bater com esta
          instalação.
        </div>
      )}

      {dados && (
        <>
          <div className="table-wrap" style={{ marginBottom: 12 }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 1 }}></th>
                  <th>O que apagar</th>
                  <th style={{ width: 150 }}>Mais velho que</th>
                </tr>
              </thead>
              <tbody>
                {dados.opcoes.map((o) => {
                  const sel = selecao[o.nome] || { marcado: false, dias: 90 };
                  return (
                    <tr key={o.nome}>
                      <td>
                        <input
                          type="checkbox"
                          checked={sel.marcado}
                          onChange={(e) =>
                            setSelecao((a) => ({
                              ...a,
                              [o.nome]: { ...sel, marcado: e.target.checked },
                            }))
                          }
                        />
                      </td>
                      <td>
                        <div className="small">
                          {o.descricao}
                          {o.pesada && (
                            <span className="pill pill-info" style={{ marginLeft: 6 }}>
                              libera mais
                            </span>
                          )}
                        </div>
                        <div className="small muted mono">--{o.nome}</div>
                      </td>
                      <td>
                        <div className="stack-h" style={{ gap: 6 }}>
                          <input
                            type="number"
                            min={0}
                            max={3650}
                            value={sel.dias}
                            disabled={!sel.marcado}
                            onChange={(e) =>
                              setSelecao((a) => ({
                                ...a,
                                [o.nome]: { ...sel, dias: e.target.value },
                              }))
                            }
                            style={{ width: 90 }}
                          />
                          <span className="small muted">dias</span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {temZero && (
            <div
              className="card card-tight"
              style={{ background: "var(--red-bg)", borderColor: "#f3b6b6", marginBottom: 12 }}
            >
              <span className="small" style={{ color: "#8c1c1c" }}>
                <IconAlerta size={13} /> Há item com <strong>0 dias</strong>. Isso
                apaga <strong>TODOS</strong> os registros daquele tipo, não só os
                antigos.
              </span>
            </div>
          )}

          {resultado && (
            <div
              className="card card-tight"
              style={{ background: "var(--green-bg)", borderColor: "#a8e0cd", marginBottom: 12 }}
            >
              <div className="small" style={{ color: "#06694a" }}>
                Limpeza concluída em {Math.round((resultado.duracao_ms || 0) / 1000)}s.
              </div>
              {resultado.saida && (
                <div className="log" style={{ marginTop: 8, maxHeight: 160 }}>
                  {resultado.saida}
                </div>
              )}
            </div>
          )}

          <div className="stack-h">
            <span className="small muted" style={{ flex: 1 }}>
              {itens.length
                ? `${itens.length} tipo(s) selecionado(s).`
                : "Marque o que quer apagar e por quanto tempo guardar."}
            </span>
            {has("cleanup.run") && (
              <button
                className="btn btn-danger"
                disabled={!itens.length || dados.em_andamento}
                onClick={() => setConfirmando(true)}
              >
                Apagar eventos antigos
              </button>
            )}
          </div>
        </>
      )}

      {confirmando && (
        <ConfirmarDigitando
          titulo="Apagar eventos antigos"
          palavra={hostNome}
          rotuloBotao="Apagar definitivamente"
          aviso={
            `Isto APAGA de ${hostNome}: ` +
            itens
              .map((i) => `${i.opcao} mais velho que ${i.dias} dia(s)`)
              .join("; ") +
            ". Não há lixeira e nenhum backup essencial recupera isso. " +
            "A operação pode levar horas em base grande, e durante ela o " +
            "painel recusa reiniciar container neste servidor."
          }
          onConfirmar={async (confirmacao) => {
            const r = await api.limpezaExecutar(hostId, {
              itens,
              confirmar_host: confirmacao,
            });
            setResultado(r);
            setSelecao({});
            await carregar();
          }}
          onFechar={() => setConfirmando(false)}
        />
      )}
    </div>
  );
}

function Faxina() {
  const { has } = usePermissions();
  const [previa, setPrevia] = useState(null);
  const [erro, setErro] = useState("");
  const [rodando, setRodando] = useState(false);
  const [feito, setFeito] = useState(null);

  const carregar = useCallback(async () => {
    try {
      setPrevia(await api.faxinaPrevia());
    } catch (ex) {
      setErro(ex.message);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function executar() {
    setRodando(true);
    setErro("");
    try {
      setFeito(await api.faxinaExecutar());
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setRodando(false);
    }
  }

  if (!previa) return null;

  const r = previa.retencoes || {};
  const totalBytes = (previa.gravacoes_bytes || 0) + (previa.staging_bytes || 0);
  const temAlgo =
    previa.gravacoes > 0 ||
    previa.staging > 0 ||
    previa.auditoria > 0 ||
    previa.logs_execucao > 0;

  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 4 }}>
        Faxina do painel
      </div>
      <div className="small muted" style={{ marginBottom: 14 }}>
        Roda sozinha uma vez por dia. Impede o painel de crescer sem fim.
        O artefato de backup não é tocado aqui — tem retenção própria, por
        destino.
      </div>

      <Erro mensagem={erro} />

      <div className="table-wrap" style={{ marginBottom: 12 }}>
        <table>
          <thead>
            <tr>
              <th>O que</th>
              <th className="right">A remover agora</th>
              <th>Retenção</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Gravações do terminal</td>
              <td className="right mono">
                {previa.gravacoes} ({formatBytes(previa.gravacoes_bytes)})
              </td>
              <td className="small muted">{r.gravacoes_dias} dias</td>
            </tr>
            <tr>
              <td>
                Staging órfão
                <div className="small muted">
                  sobra de execução que falhou no meio
                </div>
              </td>
              <td className="right mono">
                {previa.staging} ({formatBytes(previa.staging_bytes)})
              </td>
              <td className="small muted">24 horas</td>
            </tr>
            <tr>
              <td>
                Registros de auditoria
                <div className="small muted">
                  nível crítico fica o triplo do prazo
                </div>
              </td>
              <td className="right mono">
                {previa.auditoria} de {previa.auditoria_total}
              </td>
              <td className="small muted">{r.auditoria_dias} dias</td>
            </tr>
            <tr>
              <td>
                Log das execuções
                <div className="small muted">
                  esvazia o texto, mantém a linha do histórico
                </div>
              </td>
              <td className="right mono">{previa.logs_execucao}</td>
              <td className="small muted">{r.log_execucao_dias} dias</td>
            </tr>
          </tbody>
        </table>
      </div>

      {feito && (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "#a8e0cd", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "#06694a" }}>
            Faxina concluída em {feito.duracao_s}s —{" "}
            {feito.gravacoes_removidas} gravação(ões),{" "}
            {feito.staging_removido} staging, {feito.auditoria_removida}{" "}
            registro(s), {feito.logs_esvaziados} log(s) esvaziado(s).
            {feito.erros && feito.erros.length > 0 && (
              <div style={{ color: "#8c1c1c", marginTop: 4 }}>
                {feito.erros.join(" · ")}
              </div>
            )}
          </span>
        </div>
      )}

      <div className="stack-h">
        <span className="small muted" style={{ flex: 1 }}>
          {temAlgo
            ? `Liberaria ${formatBytes(totalBytes)} em disco agora.`
            : "Nada a remover no momento."}{" "}
          Os prazos ficam em <strong>Configurações → Faxina automática</strong>.
        </span>
        {has("maintenance.apply") && (
          <button
            className="btn btn-secondary"
            onClick={executar}
            disabled={rodando || !temAlgo}
          >
            {rodando ? "Executando…" : "Executar agora"}
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Limpeza pontual do painel.
 *
 * A faxina diária resolve o regime; não resolve o caso pontual — "o disco
 * encheu por causa das gravações de terminal, quero só elas, e só as de mais
 * de 180 dias". Sem esta tela, o caminho era mexer na retenção configurada,
 * que vale para TODO dia — e alguém esquece de voltar.
 *
 * Três cercas, porque apagar histórico não tem volta: simulação primeiro,
 * piso de idade no servidor e confirmação por digitação.
 */
const CATEGORIAS_LIMPEZA = [
  {
    id: "gravacoes",
    rotulo: "Gravações do InTerminal",
    nota: "arquivos .cast no disco do painel",
    campo: "gravacoes",
    bytes: "gravacoes_bytes",
  },
  {
    id: "staging",
    rotulo: "Sobras de staging de backup",
    nota: "arquivo deixado por execução que falhou no meio",
    campo: "staging",
    bytes: "staging_bytes",
  },
  {
    id: "auditoria",
    rotulo: "Registros de auditoria",
    nota: "nível crítico NUNCA sai por aqui",
    campo: "auditoria",
  },
  {
    id: "sessoes",
    rotulo: "Sessões de terminal encerradas",
    nota: "a linha do histórico; sessão aberta fica de fora",
    campo: "sessoes",
  },
  {
    id: "logs_execucao",
    rotulo: "Texto do log das execuções",
    nota: "a execução continua no histórico, sem o texto do log",
    campo: "logs_execucao",
  },
  {
    id: "amostras",
    rotulo: "Amostras do monitor",
    nota: "os pontos dos gráficos da aba Monitor",
    campo: "amostras",
  },
];

function LimpezaPontual() {
  const { has } = usePermissions();
  const [marcadas, setMarcadas] = useState({});
  const [dias, setDias] = useState(90);
  const [previa, setPrevia] = useState(null);
  const [erro, setErro] = useState("");
  const [simulando, setSimulando] = useState(false);
  const [confirmando, setConfirmando] = useState(false);
  const [feito, setFeito] = useState(null);

  const selecionadas = CATEGORIAS_LIMPEZA.filter((c) => marcadas[c.id]).map((c) => c.id);

  function alternar(id) {
    setMarcadas((atual) => ({ ...atual, [id]: !atual[id] }));
    // A prévia vale para a seleção que a gerou. Mudou a seleção, a conta
    // anterior deixou de valer — número velho ao lado de um botão de apagar
    // é o jeito mais fácil de alguém apagar o que não queria.
    setPrevia(null);
    setFeito(null);
  }

  async function simular() {
    setSimulando(true);
    setErro("");
    setFeito(null);
    try {
      setPrevia(
        await api.faxinaPontual({
          categorias: selecionadas,
          dias: Number(dias) || 0,
          simular: true,
        })
      );
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setSimulando(false);
    }
  }

  const marcadasComPrevia = CATEGORIAS_LIMPEZA.filter((c) => marcadas[c.id]);
  const total = previa
    ? marcadasComPrevia.reduce((s, c) => s + (previa[c.campo] || 0), 0)
    : 0;
  const bytes = previa
    ? marcadasComPrevia.reduce((s, c) => s + (c.bytes ? previa[c.bytes] || 0 : 0), 0)
    : 0;

  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 4 }}>
        Limpeza pontual
      </div>
      <div className="small muted" style={{ marginBottom: 14 }}>
        Escolha o que sai e a partir de quantos dias. Não mexe na retenção
        automática, não toca em artefato de backup e não apaga cadastro,
        agendamento, destino nem usuário — só histórico e sobra de disco.
      </div>

      <Erro mensagem={erro} />

      <div className="stack-v" style={{ gap: 6, marginBottom: 14 }}>
        {CATEGORIAS_LIMPEZA.map((c) => (
          <label key={c.id} className="stack-h" style={{ alignItems: "flex-start", gap: 8 }}>
            <input
              type="checkbox"
              checked={Boolean(marcadas[c.id])}
              onChange={() => alternar(c.id)}
              style={{ marginTop: 3 }}
            />
            <span style={{ flex: 1 }}>
              {c.rotulo}
              <div className="small muted">{c.nota}</div>
            </span>
            {previa && marcadas[c.id] && (
              <span className="mono small">
                {previa[c.campo] || 0}
                {c.bytes ? " (" + formatBytes(previa[c.bytes] || 0) + ")" : ""}
              </span>
            )}
          </label>
        ))}
      </div>

      <div className="stack-h" style={{ marginBottom: 12 }}>
        <span className="small">Mais velho que</span>
        <input
          type="number"
          min={7}
          max={3650}
          value={dias}
          onChange={(e) => {
            setDias(e.target.value);
            setPrevia(null);
            setFeito(null);
          }}
          style={{ width: 90 }}
        />
        <span className="small muted">
          dias — o servidor não aceita menos de 7, mesmo que a tela peça
        </span>
      </div>

      {previa && (
        <div
          className="card card-tight"
          style={{ background: "var(--amber-bg)", borderColor: "#f5d9a8", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "#8a4b00" }}>
            {total > 0 ? (
              <>
                Sairiam <strong>{total}</strong> item(ns) com mais de{" "}
                <strong>{previa.dias}</strong> dias
                {bytes > 0 ? <> e {formatBytes(bytes)} de disco</> : null}.
              </>
            ) : (
              <>Nada com mais de {previa.dias} dias nas categorias marcadas.</>
            )}
          </span>
        </div>
      )}

      {feito && (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "#a8e0cd", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "#06694a" }}>
            <IconOk size={13} /> Limpeza concluída — {feito.gravacoes} gravação(ões),{" "}
            {feito.staging} sobra(s) de staging, {feito.auditoria} registro(s) de
            auditoria, {feito.sessoes} sessão(ões), {feito.logs_execucao} log(s)
            esvaziado(s), {feito.amostras} amostra(s).
            {feito.erros && feito.erros.length > 0 && (
              <div style={{ color: "#8c1c1c", marginTop: 4 }}>{feito.erros.join(" · ")}</div>
            )}
          </span>
        </div>
      )}

      <div className="stack-h">
        <span className="small muted" style={{ flex: 1 }}>
          {selecionadas.length
            ? selecionadas.length + " categoria(s) marcada(s)."
            : "Marque ao menos uma categoria."}
        </span>
        <button
          className="btn btn-secondary"
          onClick={simular}
          disabled={!selecionadas.length || simulando}
        >
          {simulando ? "Contando\u2026" : "Ver o que sai"}
        </button>
        {has("maintenance.apply") && (
          <button
            className="btn btn-danger"
            onClick={() => setConfirmando(true)}
            disabled={!previa || total === 0}
          >
            Limpar
          </button>
        )}
      </div>

      {confirmando && previa && (
        <ConfirmarDigitando
          titulo="Limpeza pontual do painel"
          palavra="LIMPAR"
          rotuloBotao="Limpar"
          aviso={
            "Remove " + total + " item(ns) com mais de " + previa.dias +
            " dias em " + selecionadas.length + " categoria(s). Não há lixeira: " +
            "o que for apagado não volta. Auditoria crítica, artefato de backup " +
            "e execução em andamento ficam de fora."
          }
          onConfirmar={async () => {
            const r = await api.faxinaPontual({
              categorias: selecionadas,
              dias: Number(dias) || 0,
              simular: false,
              confirmar: "LIMPAR",
            });
            setFeito(r);
            setPrevia(null);
          }}
          onFechar={() => setConfirmando(false)}
        />
      )}
    </div>
  );
}

export default function ManutencaoView() {
  const { has } = usePermissions();
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();

  const [diag, setDiag] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [previa, setPrevia] = useState(null);
  const [confirmando, setConfirmando] = useState(null);
  const [aviso, setAviso] = useState("");
  const [destino, setDestino] = useState("");
  const [incluirAtivo, setIncluirAtivo] = useState(false);
  const abortRef = useRef(null);

  const diagnosticar = useCallback(async () => {
    if (!hostId) return;
    // Cancelável: a análise mede o crescimento do log por ~20s, e o
    // operador que se enganou de servidor não deveria ficar preso
    // esperando. Parar aborta a requisição de fato, não só a tela.
    const controlador = new AbortController();
    abortRef.current = controlador;
    setCarregando(true);
    setErro("");
    setPrevia(null);
    setAviso("");
    try {
      const d = await api.diagnostico(hostId, { signal: controlador.signal });
      setDiag(d);
      setDestino(d.destino_sugerido || "");
    } catch (ex) {
      if (ex && ex.name === "AbortError") {
        setAviso("Análise interrompida.");
      } else {
        setErro(ex.message);
        setDiag(null);
      }
    } finally {
      if (abortRef.current === controlador) abortRef.current = null;
      setCarregando(false);
    }
  }, [hostId]);

  const pararDiagnostico = useCallback(() => {
    if (abortRef.current) abortRef.current.abort();
  }, []);

  // Sair da tela ou trocar de servidor no meio da análise cancela a
  // requisição pendente — nada de resposta chegando numa tela que já mudou.
  useEffect(() => () => {
    if (abortRef.current) abortRef.current.abort();
  }, []);

  useEffect(() => {
    setDiag(null);
    setPrevia(null);
    setAviso("");
  }, [hostId]);

  async function simular(tipo) {
    setErro("");
    setAviso("");
    try {
      const r =
        tipo === "contencao"
          ? await api.contencaoLog(hostId, { simular: true })
          : await api.arquivarLog(hostId, { destino, simular: true, incluir_ativo: incluirAtivo });
      setPrevia({ tipo, ...r });
    } catch (ex) {
      setErro(ex.message);
    }
  }

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo="Cadastre um servidor primeiro" />;

  const host = hosts.find((h) => h.id === hostId);
  const crescGB = diag ? diag.crescimento_bytes_dia / 1073741824 : 0;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">Manutenção</div>
          <div className="page-sub">
            Disco e log dos servidores — diagnóstico e correção sem linha de comando
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          {carregando ? (
            <button className="btn btn-danger" onClick={pararDiagnostico}>
              Parar
            </button>
          ) : (
            <button className="btn btn-primary" onClick={diagnosticar} disabled={!hostId}>
              <IconAtualizar size={15} /> Diagnosticar
            </button>
          )}
        </div>
      </div>

      <Erro mensagem={erro} />

      {aviso && (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "#a8e0cd", marginBottom: 14 }}
        >
          <span className="small" style={{ color: "#06694a" }}>{aviso}</span>
        </div>
      )}

      {carregando && !diag && (
        <Carregando texto="Medindo crescimento do log (leva ~20s)…" />
      )}

      {!diag && !carregando && (
        <Vazio titulo="Clique em Diagnosticar">
          A análise lê o disco, mede a velocidade de crescimento do log e
          verifica o que já está configurado. <strong>Não altera nada.</strong>
        </Vazio>
      )}

      {diag && (
        <div className="stack-v">
          {/* ── Resumo ───────────────────────────────────────────── */}
          <div className="grid-stats">
            <div className="card card-tight stat">
              <span className="stat-label">Crescimento do log</span>
              <div
                className="stat-value"
                style={{ color: crescGB > 1 ? "var(--red)" : crescGB > 0.2 ? "var(--amber)" : "var(--green)" }}
              >
                {formatBytes(diag.crescimento_bytes_dia)}/dia
              </div>
              <span className="stat-sub">
                {crescGB > 1
                  ? "Alto — o disco vai encher"
                  : crescGB > 0.2
                  ? "Moderado — vale conter"
                  : "Sob controle"}
              </span>
            </div>

            <div className="card card-tight stat">
              <span className="stat-label">/var/log ocupa</span>
              <div className="stat-value">{formatBytes(diag.varlog_bytes)}</div>
              <span className="stat-sub">
                {formatBytes(diag.rotacionados_bytes)} são arquivos já rotacionados
              </span>
            </div>

            <div className="card card-tight stat">
              <span className="stat-label">Contenção aplicada</span>
              <div className="stat-value">
                {diag.contencao_aplicada && diag.contencao_aplicada.rsyslog ? (
                  <span style={{ color: "var(--green)" }}>Sim</span>
                ) : (
                  <span style={{ color: "var(--amber)" }}>Não</span>
                )}
              </div>
              <span className="stat-sub">
                driver do Docker: <span className="mono">{diag.log_driver || "?"}</span>
              </span>
            </div>
          </div>

          {/* ── Discos ───────────────────────────────────────────── */}
          <div>
            <div className="section-title">Discos</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Ponto</th>
                    <th className="right">Usado</th>
                    <th className="right">Livre</th>
                    <th style={{ width: 170 }}>Ocupação</th>
                  </tr>
                </thead>
                <tbody>
                  {diag.discos.map((d) => (
                    <tr key={d.ponto}>
                      <td className="mono">{d.ponto}</td>
                      <td className="right mono">{formatBytes(d.usado_bytes)}</td>
                      <td className="right mono">{formatBytes(d.livre_bytes)}</td>
                      <td>
                        <div className="stack-h" style={{ gap: 8 }}>
                          <div style={{ flex: 1 }}><Medidor pct={d.percentual} /></div>
                          <span
                            className="small mono"
                            style={{
                              minWidth: 42,
                              textAlign: "right",
                              color:
                                nivel(d.percentual) === "err" ? "var(--red)"
                                : nivel(d.percentual) === "warn" ? "var(--amber)"
                                : "var(--text-2)",
                            }}
                          >
                            {d.percentual}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* ── Ação 1: contenção ────────────────────────────────── */}
          <div className="card">
            <div className="section-title">1. Conter o crescimento do log</div>

            {diag.container_polui_syslog ? (
              <div className="small" style={{ marginBottom: 12 }}>
                Os containers estão gravando no <span className="mono">/var/log/syslog</span> —{" "}
                <strong>{diag.linhas_container_no_syslog} das últimas 2000 linhas</strong>.
                O filtro descarta apenas requisição HTTP bem-sucedida (2xx/3xx);
                erro e aviso continuam sendo gravados.
                <div style={{ marginTop: 6, color: "var(--green)" }}>
                  <IconOk size={13} /> Nada do FindFace reinicia — só o rsyslog e o
                  journald, que são instantâneos.
                </div>
              </div>
            ) : (
              <div className="small muted" style={{ marginBottom: 12 }}>
                Nenhuma linha de container no syslog. A contenção ainda limita o
                journald e melhora a rotação, mas o ganho é menor.
              </div>
            )}

            {diag.amostra && (
              <details style={{ marginBottom: 12 }}>
                <summary className="small muted" style={{ cursor: "pointer" }}>
                  Ver amostra do que está sendo gravado
                </summary>
                <div className="log" style={{ marginTop: 8, maxHeight: 160 }}>{diag.amostra}</div>
              </details>
            )}

            <div className="stack-h">
              <button className="btn btn-secondary" onClick={() => simular("contencao")}>
                <IconLogs size={15} /> Ver o que será alterado
              </button>
              {has("maintenance.apply") && (
                <button className="btn btn-primary" onClick={() => setConfirmando("contencao")}>
                  Aplicar contenção
                </button>
              )}
            </div>
          </div>

          {/* ── Ação 2: arquivar ─────────────────────────────────── */}
          <div className="card">
            <div className="section-title">2. Arquivar log antigo</div>
            <div className="small" style={{ marginBottom: 12 }}>
              Move os arquivos já rotacionados (
              <strong>{formatBytes(diag.rotacionados_bytes)}</strong> em{" "}
              {diag.rotacionados.length} arquivo(s)) para um disco com folga e
              comprime lá.
              <div style={{ marginTop: 6, color: "var(--green)" }}>
                <IconOk size={13} /> Nada é apagado. O ativo, se incluído, é copiado
                antes de ser zerado.
              </div>
            </div>

            <div className="row row-2">
              <div className="field">
                <label className="label">Destino</label>
                <input
                  className="mono"
                  value={destino}
                  onChange={(e) => setDestino(e.target.value)}
                  placeholder="/media/STORAGE/logs-arquivados"
                />
                <div className="field-help">
                  Sugerido pelo disco com mais espaço livre.
                </div>
              </div>
              <div className="field">
                <label className="label">Incluir o syslog ativo</label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={incluirAtivo}
                    onChange={(e) => setIncluirAtivo(e.target.checked)}
                  />
                  <span>
                    Copiar o <span className="mono">/var/log/syslog</span> atual e
                    zerá-lo com <span className="mono">truncate</span> (nunca{" "}
                    <span className="mono">rm</span> — o rsyslog o mantém aberto e
                    apagar não devolveria o espaço)
                  </span>
                </label>
              </div>
            </div>

            <div className="stack-h">
              <button
                className="btn btn-secondary"
                onClick={() => simular("arquivar")}
                disabled={!destino}
              >
                <IconLogs size={15} /> Ver o que será movido
              </button>
              {has("maintenance.apply") && (
                <button
                  className="btn btn-primary"
                  onClick={() => setConfirmando("arquivar")}
                  disabled={!destino}
                >
                  Arquivar
                </button>
              )}
            </div>
          </div>

          {/* ── Prévia ───────────────────────────────────────────── */}
          {previa && (
            <div className="card" style={{ borderColor: "var(--blue)" }}>
              <div className="section-title">
                Prévia — {previa.tipo === "contencao" ? "contenção" : "arquivamento"}{" "}
                (nada foi alterado)
              </div>

              {previa.tipo === "contencao" &&
                previa.alteracoes.map((a) => (
                  <div key={a.caminho} style={{ marginBottom: 14 }}>
                    <div className="mono small" style={{ fontWeight: 600 }}>{a.caminho}</div>
                    <div className="small muted" style={{ margin: "3px 0 6px" }}>{a.efeito}</div>
                    <div className="log" style={{ maxHeight: 200 }}>{a.conteudo}</div>
                  </div>
                ))}

              {previa.tipo === "arquivar" && (
                <>
                  <div className="small" style={{ marginBottom: 10 }}>
                    Destino <span className="mono">{previa.destino}</span> ·{" "}
                    {previa.candidatos.length} arquivo(s) ·{" "}
                    <strong>{formatBytes(previa.total_bytes)}</strong> a liberar
                  </div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>Arquivo</th>
                          <th className="right">Tamanho</th>
                        </tr>
                      </thead>
                      <tbody>
                        {previa.candidatos.map((c) => (
                          <tr key={c.caminho}>
                            <td className="mono small">{c.caminho}</td>
                            <td className="right mono">{formatBytes(c.bytes)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {diag && <Limpeza hostId={hostId} hostNome={host ? host.name : ""} />}

      <Faxina />

      <LimpezaPontual />

      {confirmando && host && (
        <ConfirmarDigitando
          titulo={
            confirmando === "contencao"
              ? "Aplicar contenção de log"
              : "Arquivar log antigo"
          }
          palavra={host.name}
          rotuloBotao={confirmando === "contencao" ? "Aplicar" : "Arquivar"}
          aviso={
            confirmando === "contencao"
              ? `Escreve três arquivos de configuração em ${host.name} e reinicia o rsyslog e o journald. O FindFace NÃO é afetado — nenhum container reinicia. A configuração do rsyslog é validada antes; se falhar, o filtro é removido e nada reinicia.`
              : `Move ${formatBytes(diag.rotacionados_bytes)} de log rotacionado em ${host.name} para ${destino}. Nada é apagado.${incluirAtivo ? " O syslog ativo será copiado e depois zerado." : ""}`
          }
          onConfirmar={async (confirmacao) => {
            const corpo =
              confirmando === "contencao"
                ? { simular: false, confirmar_host: confirmacao }
                : {
                    destino,
                    simular: false,
                    incluir_ativo: incluirAtivo,
                    confirmar_host: confirmacao,
                  };
            const r =
              confirmando === "contencao"
                ? await api.contencaoLog(hostId, corpo)
                : await api.arquivarLog(hostId, corpo);

            setPrevia(null);
            if (confirmando === "contencao") {
              setAviso(
                "Contenção aplicada. Rode o diagnóstico de novo em alguns minutos — " +
                  "o crescimento por dia é o número que prova se funcionou."
              );
            } else {
              setAviso(
                `Arquivamento concluído. Livre em / : ${formatBytes(r.livre_antes)} → ` +
                  `${formatBytes(r.livre_depois)}. A compressão segue em segundo plano.`
              );
            }
            await diagnosticar();
          }}
          onFechar={() => setConfirmando(null)}
        />
      )}
    </>
  );
}
