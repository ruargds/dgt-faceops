import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { t } from "../../i18n";
import { Carregando, Erro, SeletorHost, Vazio, useHosts } from "../Comuns";
import { IconAtualizar, IconLixeira, IconLogs, IconMais, IconStop } from "../Icons";

const MAX_LINHAS = 2000;

/**
 * Extrai um campo por caminho com ponto, como o jq faz: `context.dgtId`
 * navega o objeto aninhado. Devolve "-" quando falta, em vez de vazio —
 * coluna vazia num terminal deixa a linha ilegível.
 */
function extrair(objeto, caminho) {
  const partes = caminho.split(".");
  let atual = objeto;
  for (const p of partes) {
    if (atual === null || atual === undefined) return null;
    atual = atual[p];
  }
  return atual;
}

function formatar(linha, visao) {
  let obj = null;
  try {
    obj = JSON.parse(linha);
  } catch {
    obj = null;
  }

  if (obj === null || typeof obj !== "object") {
    return visao.mostrar_nao_json ? { texto: linha, json: false } : null;
  }

  // Equivale ao `select(.campo)` do jq
  for (const exigido of visao.exigir_campos || []) {
    const v = extrair(obj, exigido);
    if (v === null || v === undefined || v === "") return null;
  }

  const campos = visao.campos || [];
  if (!campos.length) return { texto: linha, json: true };

  const partes = campos.map((c) => {
    let v = extrair(obj, c.caminho);
    if (v === null || v === undefined) return "-";
    if (typeof v === "object") v = JSON.stringify(v);
    else v = String(v);
    if (c.corte_inicio !== null && c.corte_inicio !== undefined) {
      v = v.slice(c.corte_inicio, c.corte_fim ?? undefined);
    }
    return v === "" ? "-" : v;
  });

  return { texto: partes.join("  |  "), json: true };
}

export default function LogsView() {
  const { hosts, hostId, setHostId, carregando: carregandoHosts } = useHosts();

  const [containers, setContainers] = useState([]);
  const [container, setContainer] = useState("");
  const [visoes, setVisoes] = useState([]);
  const [visaoId, setVisaoId] = useState(null);
  const [linhas, setLinhas] = useState([]);
  const [estado, setEstado] = useState("parado");
  const [erro, setErro] = useState("");
  const [filtroRapido, setFiltroRapido] = useState("");
  const [autoScroll, setAutoScroll] = useState(true);
  const [descartadas, setDescartadas] = useState(0);
  const [salvando, setSalvando] = useState(false);

  const wsRef = useRef(null);
  const fimRef = useRef(null);
  const visaoRef = useRef(null);

  const visao = visoes.find((v) => v.id === visaoId) || {
    campos: [],
    exigir_campos: [],
    destacar: "",
    mostrar_nao_json: true,
    tail: 200,
  };
  visaoRef.current = visao;

  const regexDestaque = visao.destacar
    ? new RegExp(visao.destacar, "i")
    : null;

  // ── Carregamento ────────────────────────────────────────────────────

  useEffect(() => {
    api.visoesLog().then(setVisoes).catch((e) => setErro(e.message));
  }, []);

  const carregarContainers = useCallback(async () => {
    if (!hostId) return;
    setErro("");
    try {
      const lista = await api.containersLog(hostId);
      setContainers(lista);
      // Se a visão salva aponta para um container que existe aqui,
      // seleciona sozinho — é o caso mais comum.
      const alvo = visaoRef.current && visaoRef.current.container;
      setContainer((atual) => {
        if (alvo && lista.some((c) => c.nome === alvo)) return alvo;
        if (atual && lista.some((c) => c.nome === atual)) return atual;
        return lista.length ? lista[0].nome : "";
      });
    } catch (ex) {
      setErro(ex.message);
      setContainers([]);
    }
  }, [hostId]);

  useEffect(() => {
    carregarContainers();
  }, [carregarContainers]);

  // ── Stream ──────────────────────────────────────────────────────────

  const parar = useCallback(() => {
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* já fechando */
      }
      wsRef.current = null;
    }
    setEstado("parado");
  }, []);

  const iniciar = useCallback(async () => {
    if (!hostId || !container) return;
    parar();
    setErro("");
    setLinhas([]);
    setDescartadas(0);
    setEstado("conectando");

    let ticket;
    try {
      const r = await api.ticketLog(hostId, container, visaoRef.current.tail ?? 200);
      ticket = r.ticket;
    } catch (ex) {
      setErro(ex.message);
      setEstado("erro");
      return;
    }

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(
      `${proto}://${window.location.host}/api/logs/ws?ticket=${encodeURIComponent(ticket)}`
    );
    wsRef.current = ws;

    ws.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.tipo === "linha") {
        const f = formatar(msg.dados, visaoRef.current);
        if (f === null) return;
        setLinhas((atual) => {
          const novo = [...atual, { ...f, id: Math.random() }];
          // Teto de linhas: a aba trava muito antes do servidor
          return novo.length > MAX_LINHAS ? novo.slice(-MAX_LINHAS) : novo;
        });
      } else if (msg.tipo === "pronto") {
        setEstado("seguindo");
      } else if (msg.tipo === "descartadas") {
        setDescartadas(msg.n);
      } else if (msg.tipo === "erro") {
        setErro(msg.mensagem);
        setEstado("erro");
      } else if (msg.tipo === "fim") {
        setEstado("parado");
      }
    };
    ws.onerror = () => {
      setErro("Falha na conexão do WebSocket.");
      setEstado("erro");
    };
    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null;
      setEstado((a) => (a === "erro" ? "erro" : "parado"));
    };
  }, [hostId, container, parar]);

  // Fecha ao sair da tela — stream órfão mantém SSH aberto sem ninguém olhando
  useEffect(() => parar, [parar]);

  useEffect(() => {
    if (autoScroll && fimRef.current) {
      fimRef.current.scrollIntoView({ behavior: "auto", block: "end" });
    }
  }, [linhas, autoScroll]);

  // ── Visão ───────────────────────────────────────────────────────────

  async function salvarComoVisao() {
    const nome = window.prompt(
      "Nome da visão (fica salva e compartilhada com a equipe):",
      `${container} — ${hosts.find((h) => h.id === hostId)?.name || ""}`
    );
    if (!nome) return;
    setSalvando(true);
    try {
      await api.criarVisaoLog({
        nome,
        host_id: hostId,
        container,
        tail: visao.tail ?? 200,
        campos: visao.campos || [],
        exigir_campos: visao.exigir_campos || [],
        destacar: visao.destacar || "",
        mostrar_nao_json: visao.mostrar_nao_json,
      });
      setVisoes(await api.visoesLog());
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setSalvando(false);
    }
  }

  async function removerVisao(v) {
    if (!window.confirm(`Remover a visão '${v.nome}'?`)) return;
    try {
      await api.removerVisaoLog(v.id);
      setVisoes(await api.visoesLog());
      if (visaoId === v.id) setVisaoId(null);
    } catch (ex) {
      setErro(ex.message);
    }
  }

  if (carregandoHosts) return <Carregando />;
  if (!hosts.length) return <Vazio titulo={t("Cadastre um servidor primeiro")} />;

  const visiveis = filtroRapido
    ? linhas.filter((l) => l.texto.toLowerCase().includes(filtroRapido.toLowerCase()))
    : linhas;

  const seguindo = estado === "seguindo";

  return (
    <>
      <div className="page-head" style={{ marginBottom: 12 }}>
        <div>
          <div className="page-title">{t("tela.logs")}</div>
          <div className="page-sub">
            {t("tela.logs.sub")}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <button className="btn btn-secondary" onClick={carregarContainers}>
            <IconAtualizar size={15} />
          </button>
        </div>
      </div>

      <Erro mensagem={erro} />

      {/* ── Visões salvas ───────────────────────────────────────── */}
      <div className="card card-tight" style={{ marginBottom: 12 }}>
        <div className="stack-h" style={{ gap: 6, flexWrap: "wrap" }}>
          <span className="stat-label" style={{ marginRight: 4 }}>{t("Visões")}</span>
          <button
            className={`btn btn-sm ${visaoId === null ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setVisaoId(null)}
          >{t("Linha crua")}</button>
          {visoes.map((v) => (
            <span key={v.id} className="stack-h" style={{ gap: 2 }}>
              <button
                className={`btn btn-sm ${visaoId === v.id ? "btn-primary" : "btn-secondary"}`}
                onClick={() => {
                  setVisaoId(v.id);
                  if (v.container && containers.some((c) => c.nome === v.container)) {
                    setContainer(v.container);
                  }
                }}
                title={v.descricao}
              >
                {v.nome}
              </button>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => removerVisao(v)}
                title={t("Remover visão")}
                style={{ padding: "4px 5px" }}
              >
                <IconLixeira size={12} />
              </button>
            </span>
          ))}
          <button
            className="btn btn-secondary btn-sm"
            onClick={salvarComoVisao}
            disabled={!container || salvando}
            title={t("Salva o container e o formato atual como visão")}
          >
            <IconMais size={13} /> {t("Salvar visão")}</button>
        </div>
        {visao.descricao && (
          <div className="small muted" style={{ marginTop: 8 }}>{visao.descricao}</div>
        )}
      </div>

      {/* ── Controles ───────────────────────────────────────────── */}
      <div className="card card-tight" style={{ marginBottom: 12 }}>
        <div className="row row-4" style={{ gap: 10, marginBottom: 0 }}>
          <div className="field" style={{ marginBottom: 0 }}>
            <label className="label">{t("Container")}</label>
            <select value={container} onChange={(e) => setContainer(e.target.value)}>
              {containers.length === 0 && <option value="">(nenhum)</option>}
              {containers.map((c) => (
                <option key={c.nome} value={c.nome}>
                  {c.nome}
                  {c.estado !== "running" ? ` (${c.estado})` : ""}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label className="label">{t("Filtrar na tela")}</label>
            <input
              value={filtroRapido}
              onChange={(e) => setFiltroRapido(e.target.value)}
              placeholder={t("texto…")}
            />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label className="label">{t("Rolagem")}</label>
            <label className="check" style={{ marginTop: 6 }}>
              <input
                type="checkbox"
                checked={autoScroll}
                onChange={(e) => setAutoScroll(e.target.checked)}
              />
              <span>{t("Acompanhar o fim")}</span>
            </label>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label className="label">&nbsp;</label>
            {seguindo || estado === "conectando" ? (
              <button className="btn btn-danger" style={{ width: "100%" }} onClick={parar}>
                <IconStop size={15} /> {t("Parar")}</button>
            ) : (
              <button
                className="btn btn-primary"
                style={{ width: "100%" }}
                onClick={iniciar}
                disabled={!container}
              >
                <IconLogs size={15} /> {t("Seguir")}</button>
            )}
          </div>
        </div>
      </div>

      {/* ── Saída ───────────────────────────────────────────────── */}
      <div className="term-bar">
        <IconLogs size={15} />
        <span className="mono">{container || "—"}</span>
        <div className="term-bar-sep" />
        <span className="small muted">
          {visiveis.length} de {linhas.length} linha(s)
        </span>
        {descartadas > 0 && (
          <span className="term-badge term-badge-rec" title="Limite de taxa: o container gera mais rápido do que a tela aguenta">
            {descartadas} descartadas
          </span>
        )}
        <span className={`pill ${seguindo ? "pill-ok" : estado === "erro" ? "pill-err" : "pill-idle"}`}>
          {seguindo ? "seguindo" : estado}
        </span>
      </div>

      <div
        className="log"
        style={{
          borderRadius: "0 0 var(--radius-lg) var(--radius-lg)",
          maxHeight: "calc(100vh - 340px)",
          minHeight: 280,
        }}
      >
        {visiveis.length === 0 ? (
          <span className="muted">
            {seguindo
              ? "Aguardando linhas… (o container pode estar quieto)"
              : "Escolha o container e clique em Seguir."}
          </span>
        ) : (
          visiveis.map((l) => (
            <div
              key={l.id}
              style={{
                color: regexDestaque && regexDestaque.test(l.texto)
                  ? "#ff8080"
                  : l.json
                  ? "#C8D3E5"
                  : "#8898A8",
              }}
            >
              {l.texto}
            </div>
          ))
        )}
        <div ref={fimRef} />
      </div>

      <div className="small muted" style={{ marginTop: 8 }}>
        A formatação acontece no navegador — o servidor não precisa ter{" "}
        <span className="mono">jq</span> instalado, e nenhuma expressão sua chega
        perto de um shell remoto. Abrir o stream fica registrado na auditoria.
      </div>
    </>
  );
}
