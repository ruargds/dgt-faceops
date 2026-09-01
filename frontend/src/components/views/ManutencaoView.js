import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes, nivel } from "../../api";
import { t } from "../../i18n";
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
        <div className="section-title" style={{ marginBottom: 0 }}>{t("Limpeza de eventos antigos")}</div>
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
          style={{ background: "var(--amber-bg)", borderColor: "var(--amber-bd)", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "var(--amber-fg)" }}>
            Há uma limpeza em andamento neste servidor. Enquanto ela roda, o
            painel recusa reiniciar container e parar o stack — o manual é
            explícito de que isso corromperia o banco.
          </span>
        </div>
      )}

      {dados && !dados.confirmado_pelo_servidor && (
        <div className="small" style={{ color: "var(--amber)", marginBottom: 10 }}>{t("O servidor não respondeu ao")} <span className="mono">--help</span>. A
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
                  <th>{t("O que apagar")}</th>
                  <th style={{ width: 150 }}>{t("Mais velho que")}</th>
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
                            <span className="pill pill-info" style={{ marginLeft: 6 }}>{t("libera mais")}</span>
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
              style={{ background: "var(--red-bg)", borderColor: "var(--red-bd)", marginBottom: 12 }}
            >
              <span className="small" style={{ color: "var(--red-fg)" }}>
                <IconAlerta size={13} /> {t("Há item com")} <strong>{t("0 dias")}</strong>. Isso
                apaga <strong>{t("TODOS")}</strong> os registros daquele tipo, não só os
                antigos.
              </span>
            </div>
          )}

          {resultado && (
            <div
              className="card card-tight"
              style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 12 }}
            >
              <div className="small" style={{ color: "var(--green-fg)" }}>
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
              >{t("Apagar eventos antigos")}</button>
            )}
          </div>
        </>
      )}

      {confirmando && (
        <ConfirmarDigitando
          titulo={t("Apagar eventos antigos")}
          palavra={hostNome}
          rotuloBotao={t("Apagar definitivamente")}
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
      <div className="section-title" style={{ marginBottom: 4 }}>{t("Faxina do painel")}</div>
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
              <th>{t("O que")}</th>
              <th className="right">{t("A remover agora")}</th>
              <th>{t("Retenção")}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{t("Gravações do terminal")}</td>
              <td className="right mono">
                {previa.gravacoes} ({formatBytes(previa.gravacoes_bytes)})
              </td>
              <td className="small muted">{r.gravacoes_dias} dias</td>
            </tr>
            <tr>
              <td>{t("Staging órfão")}<div className="small muted">{t("sobra de execução que falhou no meio")}</div>
              </td>
              <td className="right mono">
                {previa.staging} ({formatBytes(previa.staging_bytes)})
              </td>
              <td className="small muted">{t("24 horas")}</td>
            </tr>
            <tr>
              <td>{t("Registros de auditoria")}<div className="small muted">{t("nível crítico fica o triplo do prazo")}</div>
              </td>
              <td className="right mono">
                {previa.auditoria} de {previa.auditoria_total}
              </td>
              <td className="small muted">{r.auditoria_dias} dias</td>
            </tr>
            <tr>
              <td>{t("Log das execuções")}<div className="small muted">{t("esvazia o texto, mantém a linha do histórico")}</div>
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
          style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "var(--green-fg)" }}>
            Faxina concluída em {feito.duracao_s}s —{" "}
            {feito.gravacoes_removidas} gravação(ões),{" "}
            {feito.staging_removido} staging, {feito.auditoria_removida}{" "}
            registro(s), {feito.logs_esvaziados} log(s) esvaziado(s).
            {feito.erros && feito.erros.length > 0 && (
              <div style={{ color: "var(--red-fg)", marginTop: 4 }}>
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
          Os prazos ficam em <strong>{t("Configurações → Faxina automática")}</strong>.
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
      <div className="section-title" style={{ marginBottom: 4 }}>{t("Limpeza pontual")}</div>
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
        <span className="small">{t("Mais velho que")}</span>
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
          style={{ background: "var(--amber-bg)", borderColor: "var(--amber-bd)", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "var(--amber-fg)" }}>
            {total > 0 ? (
              <>{t("Sairiam")} <strong>{total}</strong> item(ns) com mais de{" "}
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
          style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "var(--green-fg)" }}>
            <IconOk size={13} /> Limpeza concluída — {feito.gravacoes} gravação(ões),{" "}
            {feito.staging} sobra(s) de staging, {feito.auditoria} registro(s) de
            auditoria, {feito.sessoes} sessão(ões), {feito.logs_execucao} log(s)
            esvaziado(s), {feito.amostras} amostra(s).
            {feito.erros && feito.erros.length > 0 && (
              <div style={{ color: "var(--red-fg)", marginTop: 4 }}>{feito.erros.join(" · ")}</div>
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
          >{t("Limpar")}</button>
        )}
      </div>

      {confirmando && previa && (
        <ConfirmarDigitando
          titulo={t("Limpeza pontual do painel")}
          palavra="LIMPAR"
          rotuloBotao={t("Limpar")}
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

/**
 * Rotatividade do próprio FindFace.
 *
 * O que enche o disco num servidor de reconhecimento facial não é o log:
 * são as fotos de evento — num servidor real, 242 GB de 268 GB. E o
 * FindFace sabe se limpar sozinho: tem política de retenção própria, com
 * idade máxima por tipo de evento, por quadro completo e por cluster.
 *
 * Até aqui o painel só oferecia a limpeza destrutiva, que apaga o passado
 * e não impede o disco de encher de novo na semana seguinte. Isto ataca a
 * causa; a limpeza ataca o sintoma. As duas coisas convivem — e é por isso
 * que esta seção fica logo acima da limpeza.
 *
 * Prazo em DIAS. A API do fabricante fala em segundos, e pedir segundos a
 * quem opera é convite a apagar cinco anos achando que apagou cinco dias.
 */
function Retencao({ hostId, hostNome }) {
  const { has } = usePermissions();
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [edicao, setEdicao] = useState({});
  const [ligadas, setLigadas] = useState({});
  const [confirmando, setConfirmando] = useState(false);
  const [salvo, setSalvo] = useState("");

  const carregar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    setSalvo("");
    try {
      const r = await api.retencao(hostId);
      setDados(r);
      setEdicao({});
      setLigadas({});
    } catch (ex) {
      setDados(null);
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, [hostId]);

  useEffect(() => {
    carregar();
  }, [carregar]);

  if (carregando && !dados) return <Carregando texto="Lendo a política do FindFace…" />;

  if (erro) {
    return (
      <div className="card card-tight">
        <div className="section-title" style={{ marginBottom: 4 }}>
          Rotatividade do FindFace
        </div>
        <div className="small muted">{erro}</div>
      </div>
    );
  }
  if (!dados) return null;

  const valor = (campo) =>
    edicao[campo.chave] !== undefined ? edicao[campo.chave] : campo.dias ?? "";

  const mudou =
    Object.keys(edicao).length > 0 || Object.keys(ligadas).length > 0;

  return (
    <div className="card">
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 4 }}>
        <div className="section-title" style={{ marginBottom: 0 }}>
          Rotatividade do FindFace
        </div>
        <button className="btn btn-ghost btn-sm" onClick={carregar} disabled={carregando}>
          <IconAtualizar size={14} /> {carregando ? "Lendo…" : "Recarregar"}
        </button>
      </div>
      <div className="small muted" style={{ marginBottom: 14 }}>
        Por quanto tempo o FindFace guarda cada coisa. É a configuração da
        própria plataforma — mexer aqui é mexer lá. Reduzir um prazo não apaga
        nada no clique: o FindFace passa a remover o que ficar mais velho que o
        novo prazo, no ritmo dele. <strong>Zero</strong> significa guardar para
        sempre.
      </div>

      {salvo && (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "var(--green-fg)" }}>
            <IconOk size={13} /> {salvo}
          </span>
        </div>
      )}

      {dados.grupos.map((g) => (
        <div key={g.grupo} style={{ marginBottom: 14 }}>
          <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
            {g.grupo}
          </div>
          <div className="table-wrap">
            <table className="tabela-densa">
              <tbody>
                {g.campos.map((campo, idx) => (
                  <tr key={campo.chave}>
                    <td title={campo.chave}>
                      {campo.rotulo}
                      {idx < 2 && (
                        <span className="pill pill-warn" style={{ marginLeft: 8 }}>
                          ocupa mais disco
                        </span>
                      )}
                    </td>
                    <td className="right" style={{ width: 190 }}>
                      <div className="stack-h" style={{ justifyContent: "flex-end", gap: 6 }}>
                        <input
                          type="number"
                          min={0}
                          step={1}
                          value={valor(campo)}
                          disabled={!has("maintenance.apply")}
                          onChange={(e) =>
                            setEdicao((atual) => ({
                              ...atual,
                              [campo.chave]: e.target.value,
                            }))
                          }
                          style={{ width: 90, textAlign: "right" }}
                        />
                        <span className="small muted">dias</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {dados.video.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
            Arquivo de vídeo
            {dados.video_ligado === false && (
              <span className="pill pill-idle" style={{ marginLeft: 8 }}>
                limpeza desligada na plataforma
              </span>
            )}
          </div>
          <div className="table-wrap">
            <table className="tabela-densa">
              <tbody>
                {dados.video.map((campo) => (
                  <tr key={campo.chave}>
                    <td title={campo.chave}>{campo.rotulo}</td>
                    <td className="right" style={{ width: 190 }}>
                      <div className="stack-h" style={{ justifyContent: "flex-end", gap: 6 }}>
                        <input
                          type="number"
                          min={0}
                          step={1}
                          value={valor(campo)}
                          disabled={!has("maintenance.apply")}
                          onChange={(e) =>
                            setEdicao((atual) => ({
                              ...atual,
                              [campo.chave]: e.target.value,
                            }))
                          }
                          style={{ width: 90, textAlign: "right" }}
                        />
                        <span className="small muted">dias</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {dados.chaves.map((c) => (
        <label className="check" key={c.chave} title={c.ajuda}>
          <input
            type="checkbox"
            checked={ligadas[c.chave] !== undefined ? ligadas[c.chave] : c.ligado}
            disabled={!has("maintenance.apply")}
            onChange={(e) =>
              setLigadas((atual) => ({ ...atual, [c.chave]: e.target.checked }))
            }
          />
          <span>
            {c.rotulo}
            <div className="small muted">{c.ajuda}</div>
          </span>
        </label>
      ))}

      {has("maintenance.apply") && (
        <div className="stack-h" style={{ marginTop: 12 }}>
          <span className="small muted" style={{ flex: 1 }}>
            {mudou
              ? "Alterações não salvas."
              : "Nada alterado. Os números são os que estão valendo agora na plataforma."}
          </span>
          <button
            className="btn btn-danger"
            disabled={!mudou}
            onClick={() => setConfirmando(true)}
          >
            Salvar política
          </button>
        </div>
      )}

      {confirmando && (
        <ConfirmarDigitando
          titulo="Mudar a rotatividade do FindFace"
          palavra={hostNome}
          rotuloBotao="Salvar"
          aviso={
            `Isto altera a configuração da plataforma da NtechLab em ${hostNome}. ` +
            "Nada é apagado no clique, mas o FindFace passa a remover o que ficar " +
            "mais velho que os novos prazos — e nenhum backup essencial recupera " +
            "foto de evento. Confira os números antes."
          }
          onConfirmar={async (confirmacao) => {
            const dias = {};
            Object.entries(edicao).forEach(([k, v]) => {
              if (v !== "" && v !== null) dias[k] = Number(v);
            });
            await api.salvarRetencao(hostId, {
              dias,
              chaves: ligadas,
              confirmar_host: confirmacao,
            });
            setSalvo("Política salva. A plataforma passa a valer os novos prazos.");
            await carregar();
          }}
          onFechar={() => setConfirmando(false)}
        />
      )}
    </div>
  );
}

/**
 * Chaves do FindFace que só existem em arquivo.
 *
 * Parte do que decide o volume gravado não está na interface nem na API da
 * NtechLab: está no arquivo de configuração do serviço legacy, e o
 * procedimento oficial do manual é editar com `vi` e reiniciar os
 * containers. Isso significava abrir shell no servidor de produção para
 * mudar a hora da limpeza automática.
 *
 * O manual também avisa que **a configuração pela interface/API sobrescreve
 * o arquivo** — por isso o que dá para ajustar em Rotatividade do FindFace
 * deve ser ajustado lá, e aqui ficam só as chaves que a API não expõe.
 *
 * A tela mostra a linha exata antes e depois. O painel copia o arquivo
 * antes, compila depois e restaura sozinho se não compilar; e não reinicia
 * nada — reiniciar para o reconhecimento, e a hora é escolha de quem opera.
 */
function ConfigFindFace({ hostId, hostNome }) {
  const { has } = usePermissions();
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [valores, setValores] = useState({});
  const [previa, setPrevia] = useState(null);
  const [confirmando, setConfirmando] = useState(null);
  const [feito, setFeito] = useState(null);

  const carregar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    try {
      const r = await api.configFF(hostId);
      setDados(r);
      setValores({});
      setPrevia(null);
    } catch (ex) {
      setDados(null);
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, [hostId]);

  useEffect(() => {
    setFeito(null);
    carregar();
  }, [carregar]);

  if (carregando && !dados) return null;
  if (erro) {
    return (
      <div className="card card-tight">
        <div className="section-title" style={{ marginBottom: 4 }}>
          Configuração em arquivo do FindFace
        </div>
        <div className="small muted">{erro}</div>
      </div>
    );
  }
  if (!dados) return null;

  const valorDe = (campo) =>
    valores[campo.chave] !== undefined ? valores[campo.chave] : campo.valor;

  async function simular(campo) {
    setErro("");
    setFeito(null);
    try {
      setPrevia(
        await api.salvarConfigFF(hostId, {
          chave: campo.chave,
          valor: String(valorDe(campo)),
          simular: true,
        })
      );
    } catch (ex) {
      setPrevia(null);
      setErro(ex.message);
    }
  }

  return (
    <div className="card">
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 4 }}>
        <div className="section-title" style={{ marginBottom: 0 }}>
          Configuração em arquivo do FindFace
        </div>
        <button className="btn btn-ghost btn-sm" onClick={carregar} disabled={carregando}>
          <IconAtualizar size={14} /> Recarregar
        </button>
      </div>
      <div className="small muted" style={{ marginBottom: 12 }}>
        O que a plataforma não expõe pela interface nem pela API. Editar aqui é
        editar <span className="mono">{dados.arquivo}</span> — o painel copia
        antes, confere a sintaxe depois e restaura sozinho se algo sair errado.
        <strong> Nada é reiniciado automaticamente.</strong>
      </div>

      {feito && (
        <div
          className="card card-tight"
          style={{ background: "var(--amber-bg)", borderColor: "var(--amber-bd)", marginBottom: 12 }}
        >
          <span className="small" style={{ color: "var(--amber-fg)" }}>
            <IconAlerta size={13} /> Gravado. Cópia em{" "}
            <span className="mono">{feito.copia}</span>. {feito.aviso_reinicio}
          </span>
        </div>
      )}

      {dados.campos.map((campo) => (
        <div className="field" key={campo.chave}>
          <label className="label" title={campo.chave}>
            {campo.rotulo}
          </label>
          {campo.presente ? (
            <>
              <div className="stack-h" style={{ gap: 8 }}>
                {campo.tipo === "booleano" ? (
                  <select
                    value={valorDe(campo)}
                    disabled={!has("maintenance.apply")}
                    onChange={(e) =>
                      setValores((a) => ({ ...a, [campo.chave]: e.target.value }))
                    }
                    style={{ width: 160 }}
                  >
                    <option value="True">Ligada</option>
                    <option value="False">Desligada</option>
                  </select>
                ) : (
                  <input
                    className="mono"
                    value={valorDe(campo)}
                    disabled={!has("maintenance.apply")}
                    placeholder={campo.exemplo}
                    onChange={(e) =>
                      setValores((a) => ({ ...a, [campo.chave]: e.target.value }))
                    }
                  />
                )}
                {has("maintenance.apply") && (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => simular(campo)}
                  >
                    Ver o que muda
                  </button>
                )}
              </div>
              <div className="field-help">{campo.ajuda}</div>
              <div className="small muted mono" style={{ marginTop: 4 }}>
                linha {campo.linha}: {campo.conteudo}
              </div>
            </>
          ) : (
            <div className="small muted">
              Não existe no arquivo desta instalação — o painel não cria chave
              nova em arquivo de fabricante.
            </div>
          )}
        </div>
      ))}

      {previa && previa.mudou && previa.simulado && (
        <div className="card card-tight" style={{ marginTop: 10 }}>
          <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
            Linha {previa.linha} de {previa.arquivo}
          </div>
          <div className="mono small" style={{ color: "var(--red)" }}>
            − {previa.antes}
          </div>
          <div className="mono small" style={{ color: "var(--green)" }}>
            + {previa.depois}
          </div>
          <div className="stack-h" style={{ marginTop: 10, justifyContent: "flex-end" }}>
            <button
              className="btn btn-danger btn-sm"
              onClick={() => setConfirmando(previa)}
            >
              Aplicar no servidor
            </button>
          </div>
        </div>
      )}

      {previa && !previa.mudou && (
        <div className="small muted" style={{ marginTop: 8 }}>
          {previa.mensagem || "Nada mudaria."}
        </div>
      )}

      {dados.copias.length > 0 && (
        <div className="small muted" style={{ marginTop: 10 }}>
          Cópias anteriores no servidor: {dados.copias.length}. A mais recente é{" "}
          <span className="mono">{dados.copias[0]}</span>.
        </div>
      )}

      {confirmando && (
        <ConfirmarDigitando
          titulo="Escrever na configuração do FindFace"
          palavra={hostNome}
          rotuloBotao="Aplicar"
          aviso={
            `Isto altera ${confirmando.arquivo} em ${hostNome}. O painel copia o ` +
            "arquivo antes e restaura sozinho se ele não compilar. Depois de " +
            "gravar, o FindFace só passa a valer a mudança quando os containers " +
            "forem reiniciados — o que PARA o reconhecimento por alguns minutos, " +
            "e fica por sua conta escolher a hora."
          }
          onConfirmar={async (confirmacao) => {
            const r = await api.salvarConfigFF(hostId, {
              chave: confirmando.chave || Object.keys(valores)[0],
              valor: String(
                valores[confirmando.chave || Object.keys(valores)[0]] ?? ""
              ),
              simular: false,
              confirmar_host: confirmacao,
            });
            setFeito(r);
            setPrevia(null);
            await carregar();
          }}
          onFechar={() => setConfirmando(null)}
        />
      )}
    </div>
  );
}

export default function ManutencaoView({ alvo }) {
  const { has } = usePermissions();
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();

  // Chegou de um atalho de alerta de disco cheio — abre já no host certo.
  useEffect(() => {
    if (alvo && alvo.hostId) setHostId(alvo.hostId);
  }, [alvo, setHostId]);

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
  if (!hosts.length) return <Vazio titulo={t("Cadastre um servidor primeiro")} />;

  const host = hosts.find((h) => h.id === hostId);
  const crescGB = diag ? diag.crescimento_bytes_dia / 1073741824 : 0;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.manutencao")}</div>
          <div className="page-sub">
            {t("tela.manutencao.sub")}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          {carregando ? (
            <button className="btn btn-danger" onClick={pararDiagnostico}>{t("Parar")}</button>
          ) : (
            <button className="btn btn-primary" onClick={diagnosticar} disabled={!hostId}>
              <IconAtualizar size={15} /> {t("Diagnosticar")}</button>
          )}
        </div>
      </div>

      <Erro mensagem={erro} />

      {aviso && (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 14 }}
        >
          <span className="small" style={{ color: "var(--green-fg)" }}>{aviso}</span>
        </div>
      )}

      {carregando && !diag && (
        <Carregando texto="Medindo crescimento do log (leva ~20s)…" />
      )}

      {!diag && !carregando && (
        <Vazio titulo={t("Clique em Diagnosticar")}>
          A análise lê o disco, mede a velocidade de crescimento do log e
          verifica o que já está configurado. <strong>{t("Não altera nada.")}</strong>
        </Vazio>
      )}

      {diag && (
        <div className="stack-v">
          {/* ── Resumo ───────────────────────────────────────────── */}
          <div className="grid-stats">
            <div className="card card-tight stat">
              <span className="stat-label">{t("Crescimento do log")}</span>
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
              <span className="stat-label">{t("Contenção aplicada")}</span>
              <div className="stat-value">
                {diag.contencao_aplicada && diag.contencao_aplicada.rsyslog ? (
                  <span style={{ color: "var(--green)" }}>{t("Sim")}</span>
                ) : (
                  <span style={{ color: "var(--amber)" }}>{t("Não")}</span>
                )}
              </div>
              <span className="stat-sub">
                driver do Docker: <span className="mono">{diag.log_driver || "?"}</span>
              </span>
            </div>
          </div>

          {/* ── Discos ───────────────────────────────────────────── */}
          <div>
            <div className="section-title">{t("Discos")}</div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("Ponto")}</th>
                    <th className="right">{t("Usado")}</th>
                    <th className="right">{t("Livre")}</th>
                    <th style={{ width: 170 }}>{t("Ocupação")}</th>
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
            <div className="section-title">{t("1. Conter o crescimento do log")}</div>

            {diag.container_polui_syslog ? (
              <div className="small" style={{ marginBottom: 12 }}>{t("Os containers estão gravando no")} <span className="mono">/var/log/syslog</span> —{" "}
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
                <summary className="small muted" style={{ cursor: "pointer" }}>{t("Ver amostra do que está sendo gravado")}</summary>
                <div className="log" style={{ marginTop: 8, maxHeight: 160 }}>{diag.amostra}</div>
              </details>
            )}

            <div className="stack-h">
              <button className="btn btn-secondary" onClick={() => simular("contencao")}>
                <IconLogs size={15} /> {t("Ver o que será alterado")}</button>
              {has("maintenance.apply") && (
                <button className="btn btn-primary" onClick={() => setConfirmando("contencao")}>{t("Aplicar contenção")}</button>
              )}
            </div>
          </div>

          {/* ── Ação 2: arquivar ─────────────────────────────────── */}
          <div className="card">
            <div className="section-title">{t("2. Arquivar log antigo")}</div>
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
                <label className="label">{t("Destino")}</label>
                <input
                  className="mono"
                  value={destino}
                  onChange={(e) => setDestino(e.target.value)}
                  placeholder="/media/STORAGE/logs-arquivados"
                />
                <div className="field-help">{t("Sugerido pelo disco com mais espaço livre.")}</div>
              </div>
              <div className="field">
                <label className="label">{t("Incluir o syslog ativo")}</label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={incluirAtivo}
                    onChange={(e) => setIncluirAtivo(e.target.checked)}
                  />
                  <span>{t("Copiar o")} <span className="mono">/var/log/syslog</span> {t("atual e zerá-lo com")} <span className="mono">truncate</span> (nunca{" "}
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
                <IconLogs size={15} /> {t("Ver o que será movido")}</button>
              {has("maintenance.apply") && (
                <button
                  className="btn btn-primary"
                  onClick={() => setConfirmando("arquivar")}
                  disabled={!destino}
                >{t("Arquivar")}</button>
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
                  <div className="small" style={{ marginBottom: 10 }}>{t("Destino")} <span className="mono">{previa.destino}</span> ·{" "}
                    {previa.candidatos.length} arquivo(s) ·{" "}
                    <strong>{formatBytes(previa.total_bytes)}</strong> {t("a liberar")}</div>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th>{t("Arquivo")}</th>
                          <th className="right">{t("Tamanho")}</th>
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

      {hostId && <Retencao hostId={hostId} hostNome={host ? host.name : ""} />}

      {hostId && <ConfigFindFace hostId={hostId} hostNome={host ? host.name : ""} />}

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
