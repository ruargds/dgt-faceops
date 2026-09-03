import React, { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatData } from "../../api";
import { t } from "../../i18n";
import {
  ajudaDeBusca, casaBuscaExata, pontuacaoBusca, termosDaBusca,
} from "../../utils/buscaInteligente";
import { Carregando, Erro, Medidor, SeletorHost, Vazio, useHosts } from "../Comuns";
import { BarraMetrica } from "../Graficos";
import { IconAtualizar, IconDownload, IconAlerta, IconOk, IconAgenda } from "../Icons";

const PERIODOS = [
  { id: "hora", rotulo: "1 hora" },
  { id: "dia", rotulo: "24 horas" },
  { id: "semana", rotulo: "7 dias" },
  { id: "mes", rotulo: "30 dias" },
];

/**
 * Câmeras cadastradas no FindFace: quantas, quando falaram, quanto geram.
 *
 * Consulta pesada e sob demanda — lê o banco do FindFace e agrega. Nunca
 * fica atualizando sozinha: contar evento a cada minuto seria o peso que
 * o painel promete não criar.
 */
/**
 * Licenciamento do FindFace.
 *
 * É a mesma tela de licenças da plataforma da NtechLab, trazida para cá:
 * identificação e validade no topo, e a tabela de recursos com o que está
 * **em uso** e o que está **liberado**. Existe porque "cabem quantas
 * câmeras ainda?" e "estamos estourando algum limite?" eram perguntas que
 * só tinham resposta entrando na interface do fabricante.
 *
 * O número de uso muda o tempo todo, então há botão de reler — a leitura é
 * barata (um GET, sem SSH), mas não fica se atualizando sozinha: quem
 * quiser o número de agora pede o número de agora.
 *
 * Recurso estourado (usado > liberado, como o `Objects TNT API` a
 * 2.400.054 de 2.400.000 na instalação real) sobe para o topo e aparece em
 * vermelho: é o que trava operação sem avisar ninguém.
 */
function Licenciamento({ dados, erro, lendo, onAtualizar, ritmo, hostId }) {
  const [verBruto, setVerBruto] = useState(false);

  if (erro) {
    return (
      <div className="card card-tight">
        <div className="stack-h" style={{ justifyContent: "space-between" }}>
          <div className="section-title" style={{ marginBottom: 0 }}>{t("Licenciamento")}</div>
          <button className="btn btn-ghost btn-sm" onClick={onAtualizar} disabled={lendo}>
            <IconAtualizar size={14} /> {lendo ? "Lendo…" : "Tentar de novo"}
          </button>
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>{erro}</div>
      </div>
    );
  }
  if (!dados) {
    return lendo ? <Carregando texto={t("Lendo o licenciamento do FindFace…")} /> : null;
  }

  const cab = dados.cabecalho || {};
  // Estourado primeiro; depois o mais ocupado. Quem abre a tela precisa ver
  // o problema sem procurar.
  const itens = [...(dados.itens || [])].sort((a, b) => {
    if (Boolean(b.estourado) !== Boolean(a.estourado)) return b.estourado ? 1 : -1;
    const oa = a.limite > 0 && a.usado != null ? a.usado / a.limite : -1;
    const ob = b.limite > 0 && b.usado != null ? b.usado / b.limite : -1;
    return ob - oa;
  });

  const numero = (v) =>
    v === null || v === undefined ? "—" : Number(v).toLocaleString("pt-BR");

  return (
    <div className="card">
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <div className="section-title" style={{ marginBottom: 0 }}>
          Licenciamento
          {dados.estourados > 0 && (
            <span className="pill pill-err" style={{ marginLeft: 8 }}>
              <IconAlerta size={11} /> {dados.estourados} limite(s) estourado(s)
            </span>
          )}
          {dados.estourados === 0 && cab.valido && (
            <span className="pill pill-ok" style={{ marginLeft: 8 }}>
              <IconOk size={11} /> {t("válida")}</span>
          )}
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onAtualizar} disabled={lendo}>
          <IconAtualizar size={14} /> {lendo ? "Lendo…" : "Atualizar uso"}
        </button>
      </div>

      {/* A explicação de cada número fica no `title`, não na tela: três
          linhas de legenda por cartão viram parede de texto, e quem opera
          já sabe o que é "validade". Quem não sabe para o mouse em cima. */}
      <div className="grid-stats" style={{ marginBottom: 12 }}>
        <div
          className="card card-tight stat"
          title={`Identificador da licença no FindFace${cab.tipo ? ` · tipo ${cab.tipo}` : ""}`}
        >
          <span className="stat-label">{t("Identificação")}</span>
          <div className="mono small" style={{ wordBreak: "break-all" }}>
            {cab.id || "—"}
          </div>
        </div>
        <div
          className="card card-tight stat"
          title={
            cab.valido === false
              ? "Esta licença está inválida"
              : cab.dias_para_expirar != null
              ? `Faltam ${cab.dias_para_expirar} dia(s) para expirar`
              : "Data de expiração da licença"
          }
        >
          <span className="stat-label">{t("Validade")}</span>
          <div
            className="stat-value"
            style={cab.expira_em_breve ? { color: "var(--amber)" } : undefined}
          >
            {cab.validade || "—"}
          </div>
          {cab.expira_em_breve && (
            <span className="stat-sub" style={{ color: "var(--amber)" }}>
              expira em {cab.dias_para_expirar} dia(s)
            </span>
          )}
        </div>
        <div
          className="card card-tight stat"
          title="Arquivo .lic no servidor do FindFace"
        >
          <span className="stat-label">{t("Arquivo")}</span>
          <div className="mono small" style={{ wordBreak: "break-all" }}>
            {cab.arquivo || "—"}
          </div>
        </div>
        <div
          className="card card-tight stat"
          title={
            "Câmeras cadastradas no FindFace, contadas no momento desta leitura. " +
            "Detector externo é sistema de fora empurrando evento pela API: " +
            "aparece na mesma lista e consome licença, mas não é câmera."
          }
        >
          <span className="stat-label">{t("Câmeras cadastradas")}</span>
          <div className="stat-value">
            {dados.cameras_cadastradas === null || dados.cameras_cadastradas === undefined
              ? "—"
              : dados.cameras_cadastradas}
          </div>
          {dados.detectores_externos != null && (
            <span className="stat-sub">
              {dados.cameras_reais != null ? `${dados.cameras_reais} câmera(s) · ` : ""}
              {dados.detectores_externos} detector(es) externo(s)
            </span>
          )}
        </div>
      </div>

      {itens.length === 0 ? (
        <div className="small muted">
          A licença respondeu, mas nenhum limite reconhecível veio no corpo.
          Veja o conteúdo bruto abaixo.
        </div>
      ) : (
        <div className="table-wrap">
          <table className="tabela-densa">
            <thead>
              <tr>
                <th>{t("Recurso")}</th>
                <th className="right">{t("Em uso")}</th>
                <th className="right">{t("Liberado")}</th>
                <th className="right">{t("Livre")}</th>
                <th style={{ width: 150 }}>{t("Ocupação")}</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((i, idx) => {
                const pct =
                  i.limite > 0 && i.usado !== null && i.usado !== undefined
                    ? (i.usado / i.limite) * 100
                    : null;
                return (
                  <tr key={`${i.recurso}-${idx}`}>
                    <td>
                      {i.recurso}
                      {i.estourado && (
                        <span className="pill pill-err" style={{ marginLeft: 8 }}>
                          estourado
                        </span>
                      )}
                    </td>
                    <td
                      className="right mono"
                      style={i.estourado ? { color: "var(--red)", fontWeight: 600 } : undefined}
                    >
                      {numero(i.usado)}
                    </td>
                    <td className="right mono">
                      {i.ilimitado ? "ilimitado" : numero(i.limite)}
                    </td>
                    <td
                      className="right mono"
                      style={i.restante < 0 ? { color: "var(--red)" } : undefined}
                    >
                      {i.ilimitado ? "—" : numero(i.restante)}
                    </td>
                    <td>
                      {pct === null ? (
                        <span className="small muted">—</span>
                      ) : (
                        <div className="stack-h" style={{ gap: 8 }}>
                          <Medidor pct={Math.min(pct, 100)} />
                          <span className="mono small">{pct.toFixed(0)}%</span>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Ritmo de consumo: a pergunta que a licenca sozinha nao responde.
          Neste ambiente o limite que aperta e o de objetos, nao o de
          camera -- 453 dispositivos entram como detector externo e o que
          consome e o Objects TNT API. */}
      {ritmo && ritmo.recursos && ritmo.recursos.some((r) => r.por_dia !== null) && (
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="tabela-densa">
            <thead>
              <tr>
                <th>Consumo observado</th>
                <th className="right">Por dia</th>
                <th className="right">Amostras</th>
                <th className="right">No ritmo atual, acaba em</th>
              </tr>
            </thead>
            <tbody>
              {ritmo.recursos
                .filter((r) => r.por_dia !== null)
                .map((r) => (
                  <tr key={r.recurso}>
                    <td className="mono">{r.recurso}</td>
                    <td className="right mono">
                      {r.por_dia > 0 ? "+" : ""}
                      {Number(r.por_dia).toLocaleString("pt-BR")}
                    </td>
                    <td className="right mono">{r.amostras}</td>
                    <td
                      className="right mono"
                      style={
                        r.dias_para_o_fim !== null && r.dias_para_o_fim < 90
                          ? { color: "var(--amber)", fontWeight: 600 }
                          : undefined
                      }
                    >
                      {r.dias_para_o_fim === null
                        ? "—"
                        : `${r.dias_para_o_fim} dia(s)`}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
          {/* Rotatividade ao lado da projeção. Antes esta informação só
              existia na tela de Manutenção, e sem ela o "acaba em N dias"
              engana: a projeção supõe que nada é apagado, quando é
              justamente a retenção do FindFace que devolve o espaço. Mesmo
              endpoint da outra tela — nada novo, só visível onde a
              pergunta é feita. */}
          <Rotatividade hostId={hostId} />

          <div className="small muted" style={{ marginTop: 6 }}>
            Medido ponta a ponta nos últimos {ritmo.dias} dias — e não pela média
            das variações diárias, que a limpeza de eventos transformaria em
            ruído ao fazer o uso cair de um dia para o outro.
          </div>
        </div>
      )}

      {dados.funcionalidades && dados.funcionalidades.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="section-title" style={{ marginBottom: 6 }}>
            {t("Módulos licenciados")}
          </div>
          <div className="stack-h" style={{ flexWrap: "wrap", gap: 6 }}>
            {dados.funcionalidades.map((f) => (
              <span
                key={f.chave}
                className={`pill ${f.ligado ? "pill-ok" : "pill-idle"}`}
                title={
                  f.ligado
                    ? "Habilitado nesta licença"
                    : "Existe no produto, mas não está habilitado nesta licença"
                }
              >
                {f.nome}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="stack-h" style={{ marginTop: 12 }}>
        <span
          className="small muted"
          style={{ flex: 1 }}
          title={
            dados.via === "ssh"
              ? "Lido de dentro do servidor, por SSH — o serviço de licença atende em localhost sem pedir login"
              : "Lido pela API HTTP do FindFace, com a credencial cadastrada"
          }
        >
          {t("Lido de")} <span className="mono">{dados.caminho}</span>
          {dados.via === "ssh" ? " (pelo servidor)" : " (pela API)"}
        </span>
        <button className="btn btn-ghost btn-sm" onClick={() => setVerBruto((v) => !v)}>
          {verBruto ? "Esconder resposta bruta" : "Ver resposta bruta"}
        </button>
      </div>

      {verBruto && (
        <pre
          className="mono small"
          style={{ marginTop: 8, maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap" }}
        >
          {JSON.stringify(dados.bruto, null, 2)}
        </pre>
      )}
    </div>
  );
}

/**
 * Quando cada câmera falou pela última vez.
 *
 * A tabela de eventos responde "quanto"; esta responde "quando" — que é a
 * pergunta de quem desconfia que uma câmera parou. Ordena as mudas
 * primeiro, porque é para vê-las que alguém abre isto.
 *
 * Duas datas, e elas não querem dizer a mesma coisa. **Última interação**
 * é o último evento que a câmera produziu: é ela que diz se o dispositivo
 * está vivo. **Cadastro** é o carimbo do registro na plataforma, que muda
 * quando alguém edita a câmera — uma câmera parada há uma semana pode ter
 * cadastro de hoje. Misturar as duas faria a tela mentir.
 */
function UltimaInteracao({ dados, buscando, filtro }) {
  if (buscando && !dados) {
    return (
      <div className="card">
        <Carregando texto="Lendo o fluxo de eventos do FindFace…" />
      </div>
    );
  }
  if (!dados) return null;

  const v = dados.varredura || {};
  const lista = (dados.cameras || []).filter(
    (c) =>
      // Antes: `includes` cru. Com 200 câmeras, procurar "12" trazia
      // 112, 120 e 212, e "camera" não achava "câmera".
      casaBuscaExata(termosDaBusca(filtro), c.nome, String(c.id))
  );

  return (
    <div className="card">
      <div
        className="stack-h"
        style={{ justifyContent: "space-between", marginBottom: 10 }}
      >
        <div className="section-title" style={{ marginBottom: 0 }}>
          Última interação por câmera
          {/* Quando NENHUM evento foi lido e houve erro, a tela não pode
              dizer "sem evento": ela não sabe. Dizer que 200 câmeras estão
              mudas quando a verdade é "não consegui perguntar" manda a
              equipe procurar defeito onde não há. */}
          {v.falhou ? (
            <span className="pill pill-err" style={{ marginLeft: 8 }}>
              <IconAlerta size={11} /> não consegui ler os eventos
            </span>
          ) : dados.sem_interacao > 0 ? (
            <span className="pill pill-warn" style={{ marginLeft: 8 }}>
              <IconAlerta size={11} /> {dados.sem_interacao} sem evento
            </span>
          ) : null}
        </div>
        <span className="small muted">
          consultado em {formatData(dados.gerado_em)}
        </span>
      </div>

      {/* O motivo, quando houve. É isto que transforma "sem evento" em
          algo acionável: qual tipo falhou e o que a API respondeu. */}
      {v.erros && Object.keys(v.erros).length > 0 && (
        <div
          className="card card-tight"
          style={{
            background: v.falhou ? "var(--red-bg)" : "var(--amber-bg)",
            borderColor: v.falhou ? "var(--red-bd)" : "var(--amber-bd)",
            marginBottom: 12,
          }}
        >
          <div className="small" style={{ color: v.falhou ? "var(--red-fg)" : "var(--amber-fg)" }}>
            <strong>
              {v.falhou
                ? "Nenhum evento pôde ser lido — o número de 'sem evento' abaixo não é confiável."
                : "Parte dos tipos de evento não pôde ser lida."}
            </strong>
            {Object.entries(v.erros).map(([tipo, msg]) => (
              <div key={tipo} className="mono" style={{ marginTop: 4 }}>
                /events/{tipo}/ — {msg}
              </div>
            ))}
          </div>
        </div>
      )}

      {v.sem_ordenacao && v.sem_ordenacao.length > 0 && (
        <div className="small muted" style={{ marginBottom: 10 }}>
          Lido sem ordenação em: {v.sem_ordenacao.join(", ")} — esta versão da
          API recusou <span className="mono">ordering</span>. A data mostrada é
          a mais recente encontrada na varredura, e pode não ser a última
          absoluta.
        </div>
      )}

      <div className="grid-stats" style={{ marginBottom: 12 }}>
        <div>
          <span className="stat-label">Cadastradas</span>
          <div className="stat-value">{dados.total_cameras}</div>
        </div>
        <div>
          <span className="stat-label">Com evento</span>
          <div className="stat-value">{dados.com_interacao}</div>
        </div>
        <div>
          <span className="stat-label">{v.falhou ? "Não verificadas" : "Sem evento"}</span>
          <div
            className="stat-value"
            style={{
              color: dados.sem_interacao ? "var(--amber)" : "var(--text-3)",
            }}
          >
            {dados.sem_interacao}
          </div>
        </div>
        <div>
          <span className="stat-label">Eventos varridos</span>
          <div className="stat-value">
            {(v.eventos_lidos || 0).toLocaleString("pt-BR")}
          </div>
        </div>
      </div>

      <div className="small muted" style={{ marginBottom: 10 }}>
        {v.requisicoes || 0} requisição(ões) à API
        {v.ate ? `, varrendo até ${formatData(v.ate)}` : ""}. Esta leitura
        acontece só neste botão — não recarrega sozinha, não entra em
        agendamento e nada dela é gravado no banco do painel.
      </div>

      {!v.completa && (
        <div
          className="card card-tight"
          style={{
            background: "var(--amber-bg)",
            borderColor: "var(--amber-bd)",
            marginBottom: 10,
          }}
        >
          <span className="small" style={{ color: "var(--amber-fg)" }}>
            A varredura parou no teto de {(v.teto || 0).toLocaleString("pt-BR")}{" "}
            eventos com câmeras ainda sem data. Quem aparece como{" "}
            <em>sem evento</em> pode ter falado antes de{" "}
            {v.ate ? formatData(v.ate) : "o intervalo varrido"} — o que não é o
            mesmo que nunca ter falado.
          </span>
        </div>
      )}

      {lista.length === 0 ? (
        <Vazio texto="Nenhuma câmera bate com o filtro." />
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("Câmera")}</th>
                <th>{t("Situação")}</th>
                <th>Última interação</th>
                <th>Tipo</th>
                <th>Cadastro</th>
              </tr>
            </thead>
            <tbody>
              {lista.map((c) => (
                <tr key={c.id}>
                  <td>
                    <div style={{ fontWeight: 500 }}>{c.nome}</div>
                    <div className="small muted mono">
                      id {c.id}
                      {c.grupo ? ` · grupo ${c.grupo}` : ""}
                    </div>
                  </td>
                  <td>
                    {!c.ativo ? (
                      <span className="pill">desativada</span>
                    ) : c.ultima_interacao ? (
                      <span className="pill pill-ok">
                        <IconOk size={11} /> ativa
                      </span>
                    ) : (
                      <span className="pill pill-warn">
                        <IconAlerta size={11} /> {v.falhou ? "não verificada" : "sem evento"}
                      </span>
                    )}
                  </td>
                  <td className="small">
                    {c.ultima_interacao ? (
                      <span className="mono">
                        {formatData(c.ultima_interacao)}
                      </span>
                    ) : (
                      <span className="muted">
                        {v.ate
                          ? `sem evento desde ${formatData(v.ate)}`
                          : v.falhou
                          ? "não verificada"
                          : "sem evento"}
                      </span>
                    )}
                  </td>
                  <td className="small muted">{c.tipo || "—"}</td>
                  <td className="small">
                    {c.cadastro_em ? (
                      <span
                        className="mono muted"
                        title={`campo '${c.cadastro_campo}' do registro da câmera`}
                      >
                        {formatData(c.cadastro_em)}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function DispositivosView() {
  const { hosts, hostId, setHostId, carregando: carregandoHosts } = useHosts();
  const [dados, setDados] = useState(null);
  const [periodo, setPeriodo] = useState("dia");
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [filtro, setFiltro] = useState("");
  const [licenca, setLicenca] = useState(null);
  const [erroLicenca, setErroLicenca] = useState("");
  const [lendoLicenca, setLendoLicenca] = useState(false);
  const [ritmo, setRitmo] = useState(null);
  // Última interação por câmera. Estado separado de propósito: é a única
  // coisa desta tela que o operador dispara e lê sem depender da contagem
  // de eventos, que é cara.
  const [interacao, setInteracao] = useState(null);
  const [erroInteracao, setErroInteracao] = useState("");
  const [buscandoInteracao, setBuscandoInteracao] = useState(false);

  const buscarInteracao = useCallback(async () => {
    if (!hostId) return;
    setBuscandoInteracao(true);
    setErroInteracao("");
    try {
      setInteracao(await api.ultimaInteracao(hostId));
    } catch (ex) {
      setErroInteracao(ex.message);
      setInteracao(null);
    } finally {
      setBuscandoInteracao(false);
    }
  }, [hostId]);

  const consultar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    try {
      setDados(await api.dispositivos(hostId, periodo));
    } catch (ex) {
      setErro(ex.message);
      setDados(null);
    } finally {
      setCarregando(false);
    }
  }, [hostId, periodo]);

  useEffect(() => {
    setDados(null);
    // Dado de outro servidor na tela é pior que tela vazia: a tabela
    // continuaria plausível, com os nomes errados.
    setInteracao(null);
    setErroInteracao("");
  }, [hostId]);

  // Licença é leitura barata (um GET, sem SSH e sem varrer evento), então
  // carrega sozinha ao trocar de servidor — diferente da contagem de
  // eventos, que continua sob demanda. Assim a tela responde algo útil
  // antes de alguém clicar em Consultar.
  const lerLicenca = useCallback(async () => {
    if (!hostId) return;
    setLendoLicenca(true);
    setErroLicenca("");
    try {
      setLicenca(await api.licencaFindFace(hostId));
    } catch (ex) {
      setLicenca(null);
      setErroLicenca(ex.message);
    } finally {
      setLendoLicenca(false);
    }
  }, [hostId]);

  useEffect(() => {
    setLicenca(null);
    lerLicenca();
  }, [lerLicenca]);

  // O ritmo vem do historico, que so existe depois da segunda leitura em
  // dias diferentes. Sem dados ainda, a secao simplesmente nao aparece --
  // melhor que uma projecao construida sobre um ponto.
  useEffect(() => {
    if (!hostId) return;
    let vivo = true;
    setRitmo(null);
    api
      .licencaHistorico(hostId, 90)
      .then((r) => vivo && setRitmo(r))
      .catch(() => {});
    return () => {
      vivo = false;
    };
  }, [hostId, licenca]);

  if (carregandoHosts) return <Carregando />;
  if (!hosts.length) return <Vazio titulo={t("Cadastre um servidor primeiro")} />;

  // Mesma régua da outra lista desta tela, e das demais telas. Com 200
  // câmeras, `includes` cru trazia 112 e 212 ao procurar 12, e "camera"
  // não achava "câmera".
  const cameras = dados
    ? dados.cameras
        .filter((c) => casaBuscaExata(termosDaBusca(filtro), c.nome, String(c.id)))
        .sort(
          (a, b) =>
            pontuacaoBusca(termosDaBusca(filtro), a.nome) -
            pontuacaoBusca(termosDaBusca(filtro), b.nome),
        )
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.cameras")}</div>
          <div className="page-sub">
            {t("tela.cameras.sub")}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <select value={periodo} onChange={(e) => setPeriodo(e.target.value)} style={{ width: "auto" }}>
            {PERIODOS.map((p) => (
              <option key={p.id} value={p.id}>{p.rotulo}</option>
            ))}
          </select>
          <button className="btn btn-primary" onClick={consultar} disabled={carregando || !hostId}>
            <IconAtualizar size={15} /> {carregando ? "Consultando…" : "Consultar"}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={buscarInteracao}
            disabled={buscandoInteracao || !hostId}
            title="Lê o fluxo de eventos e diz quando cada câmera falou pela última vez. Roda só neste clique."
          >
            <IconAgenda size={15} />{" "}
            {buscandoInteracao ? "Varrendo…" : "Última interação"}
          </button>
          {dados && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() =>
                api
                  .baixar(api.urlExportarDispositivos(hostId, periodo), `cameras-${periodo}.csv`)
                  .catch((e) => setErro(e.message))
              }
              title={t("Baixar em CSV")}
            >
              <IconDownload size={15} /> {t("Exportar")}</button>
          )}
        </div>
      </div>

      <Erro mensagem={erro} onTentar={consultar} />

      <Erro mensagem={erroInteracao} onTentar={buscarInteracao} />

      {(buscandoInteracao || interacao) && (
        <UltimaInteracao
          dados={interacao}
          buscando={buscandoInteracao}
          filtro={filtro}
        />
      )}

      <Licenciamento
        hostId={hostId}
        ritmo={ritmo}
        dados={licenca}
        erro={erroLicenca}
        lendo={lendoLicenca}
        onAtualizar={lerLicenca}
      />

      {!dados && !carregando && (
        <Vazio titulo={t("Clique em Consultar")}>
          Lê o banco do FindFace e conta os eventos por câmera. Pode levar alguns
          segundos em base grande — por isso é sob demanda, não automático.
        </Vazio>
      )}

      {carregando && !dados && <Carregando texto={t("Contando eventos no banco do FindFace…")} />}

      {dados && (
        <div className="stack-v">
          <div className="grid-stats">
            <div className="card card-tight stat">
              <span className="stat-label">{t("Câmeras cadastradas")}</span>
              <div className="stat-value">{dados.total_cameras}</div>
              <span className="stat-sub">{dados.esquema.banco}</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">Comunicando ({dados.periodo_rotulo})</span>
              <div className="stat-value" style={{ color: "var(--green)" }}>
                {dados.cameras_com_evento}
              </div>
              <span className="stat-sub">{t("geraram ao menos um evento")}</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">{t("Sem eventos")}</span>
              <div
                className="stat-value"
                style={{ color: dados.cameras_mudas > 0 ? "var(--amber)" : "var(--text-3)" }}
              >
                {dados.cameras_mudas}
              </div>
              <span className="stat-sub">{t("nada no período — pode estar offline")}</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">{t("Total de eventos")}</span>
              <div className="stat-value">{dados.total_eventos.toLocaleString("pt-BR")}</div>
              <span className="stat-sub">{t("no período")}</span>
            </div>
          </div>

          <div className="stack-h" style={{ justifyContent: "space-between" }}>
            <input
              type="search"
              value={filtro}
              onChange={(e) => setFiltro(e.target.value)}
              title={ajudaDeBusca(t("Procura no nome e no id da câmera."))}
              placeholder={t("Buscar câmera pelo nome ou id…")}
              style={{ maxWidth: 320 }}
            />
            {dados.contagem_por_camera === false && (
              <span className="small muted" title="Contar evento por camera custa uma requisicao por camera e por tipo; com centenas de dispositivos isso seria mais de mil chamadas na API de producao">
                Com {dados.total_cameras} dispositivos, a contagem foi feita no
                total — não por dispositivo.
              </span>
            )}
            {dados.estimativa && (
              <span className="small muted">
                Volume por câmera é estimativa (rateio pela participação nos eventos).
              </span>
            )}
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("Câmera")}</th>
                  <th>{t("Situação")}</th>
                  <th className="right">Eventos ({dados.periodo_rotulo})</th>
                  <th style={{ width: 140 }}>{t("Participação")}</th>
                  <th className="right">{t("Volume estimado")}</th>
                  <th>{t("Último evento")}</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map((c) => {
                  const muda = c.eventos === 0;
                  return (
                    <tr key={c.id}>
                      <td>
                        <div style={{ fontWeight: 500 }}>{c.nome}</div>
                        <div className="small muted mono">
                          id {c.id}{c.grupo ? ` · grupo ${c.grupo}` : ""}
                        </div>
                      </td>
                      <td>
                        {muda ? (
                          <span className="pill pill-warn">
                            <IconAlerta size={11} /> {t("sem eventos")}</span>
                        ) : (
                          <span className="pill pill-ok">
                            <IconOk size={11} /> ativa
                          </span>
                        )}
                      </td>
                      <td className="right mono">{c.eventos.toLocaleString("pt-BR")}</td>
                      <td>
                        <BarraMetrica rotulo="" valor={c.fatia_pct} limite={101} unidade="%" />
                      </td>
                      <td className="right mono small">
                        {c.bytes_estimados ? formatBytes(c.bytes_estimados) : "—"}
                      </td>
                      <td className="small">
                        {c.ultimo_evento ? (
                          formatData(c.ultimo_evento)
                        ) : (
                          <span className="muted">nunca</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="small muted">
            Fonte: banco <span className="mono">{dados.esquema.banco}</span>,
            tabelas de evento{" "}
            <span className="mono">{dados.esquema.tabelas_eventos.join(", ")}</span>.
            O esquema é descoberto automaticamente — se o FindFace for atualizado e
            a consulta falhar, use o botão de redescoberta.
          </div>
        </div>
      )}
    </>
  );
}

/**
 * Rotatividade do FindFace, ao lado da projeção de consumo.
 *
 * A projeção "no ritmo atual, acaba em N dias" supõe que nada é apagado.
 * Quem desmente essa suposição é a retenção do próprio FindFace — e ela
 * só existia na tela de Manutenção, longe de onde a pergunta é feita.
 *
 * Mesma rota que aquela tela usa (`/dispositivos/{id}/retencao`): nenhum
 * endpoint novo, nenhuma leitura a mais fora do clique.
 */
function Rotatividade({ hostId }) {
  const [dados, setDados] = React.useState(null);
  const [erro, setErro] = React.useState("");
  const [aberto, setAberto] = React.useState(false);

  React.useEffect(() => {
    if (!aberto || dados || !hostId) return;
    api
      .retencao(hostId)
      .then(setDados)
      .catch((ex) => setErro(ex.message));
  }, [aberto, dados, hostId]);

  const campos = [];
  for (const g of (dados && dados.grupos) || []) {
    for (const c of g.campos || []) {
      if (c.dias !== null && c.dias !== undefined) campos.push({ ...c, grupo: g.grupo });
    }
  }
  const menor = campos.length
    ? campos.reduce((a, b) => (a.dias <= b.dias ? a : b))
    : null;

  return (
    <div style={{ marginTop: 10 }}>
      <button
        type="button"
        className="btn btn-secondary btn-sm"
        onClick={() => setAberto((v) => !v)}
      >
        {aberto ? "esconder rotatividade" : "ver rotatividade (o que é apagado, e quando)"}
      </button>

      {aberto && erro && (
        <div className="small" style={{ color: "var(--red)", marginTop: 8 }}>
          {erro}
        </div>
      )}

      {aberto && !dados && !erro && (
        <div className="small muted" style={{ marginTop: 8 }}>lendo a política…</div>
      )}

      {aberto && dados && (
        <div style={{ marginTop: 8 }}>
          <div className="small muted" style={{ marginBottom: 8 }}>
            A projeção acima supõe que <strong>nada</strong> é apagado. Estes são os
            prazos que o FindFace aplica sozinho — é o que devolve espaço e
            estica o prazo real.
            {menor && (
              <> O mais curto é <strong>{menor.rotulo}</strong>, com {menor.dias} dia(s).</>
            )}
          </div>
          {campos.length === 0 ? (
            <div className="small" style={{ color: "var(--amber-fg)" }}>
              Nenhum prazo de retenção configurado — nada é apagado sozinho, e a
              projeção acima vale como está. Ajuste em Manutenção.
            </div>
          ) : (
            <div className="table-wrap">
              <table className="tabela-densa">
                <thead>
                  <tr>
                    <th>O que</th>
                    <th>Grupo</th>
                    <th className="right">Fica por</th>
                  </tr>
                </thead>
                <tbody>
                  {campos.map((c) => (
                    <tr key={c.chave}>
                      <td>{c.rotulo}</td>
                      <td className="small muted">{c.grupo}</td>
                      <td className="right mono">{c.dias} dia(s)</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="small muted" style={{ marginTop: 6 }}>
            Alterar estes prazos continua em <strong>Manutenção</strong> — aqui é
            leitura, para a projeção fazer sentido sem trocar de tela.
          </div>
        </div>
      )}
    </div>
  );
}
