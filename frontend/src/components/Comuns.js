import React, { useEffect, useState } from "react";
import { api, nivel } from "../api";
import { IconAlerta } from "./Icons";

/** Barra de uso com cor por faixa: verde <70%, âmbar <88%, vermelho acima. */
export function Medidor({ pct }) {
  const p = Math.max(0, Math.min(pct || 0, 100));
  return (
    <div className="meter">
      <div className={`meter-fill meter-${nivel(p)}`} style={{ width: `${p}%` }} />
    </div>
  );
}

export function Estatistica({ rotulo, valor, sub, pct }) {
  return (
    <div className="card card-tight stat">
      <div className="stat-top">
        <span className="stat-label">{rotulo}</span>
        {pct !== undefined && <span className="stat-sub">{pct.toFixed(1)}%</span>}
      </div>
      <div className="stat-value">{valor}</div>
      {pct !== undefined && <Medidor pct={pct} />}
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function Vazio({ titulo, children }) {
  return (
    <div className="empty">
      <div className="empty-title">{titulo}</div>
      {children}
    </div>
  );
}

export function Erro({ mensagem, onTentar }) {
  if (!mensagem) return null;
  return (
    <div className="card" style={{ borderColor: "#f3b6b6", background: "var(--red-bg)" }}>
      <div className="stack-h" style={{ color: "#8c1c1c" }}>
        <IconAlerta size={18} />
        <div style={{ flex: 1, fontSize: 13 }}>{mensagem}</div>
        {onTentar && (
          <button className="btn btn-secondary btn-sm" onClick={onTentar}>
            Tentar de novo
          </button>
        )}
      </div>
    </div>
  );
}

export function Carregando({ texto = "Carregando…" }) {
  return (
    <div className="stack-h" style={{ padding: 18, color: "var(--text-3)" }}>
      <div className="spin" /> {texto}
    </div>
  );
}

/**
 * Seletor de servidor reutilizado por várias telas.
 *
 * Seleciona o primeiro host automaticamente: obrigar um clique antes de
 * mostrar qualquer coisa deixa a tela parecendo vazia na primeira visita.
 */
export function useHosts(selecionarPrimeiro = true) {
  const [hosts, setHosts] = useState([]);
  const [hostId, setHostId] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    let vivo = true;
    api
      .hosts()
      .then((lista) => {
        if (!vivo) return;
        const ativos = lista.filter((h) => h.enabled);
        setHosts(lista);
        if (selecionarPrimeiro && ativos.length) setHostId(ativos[0].id);
      })
      .catch((e) => vivo && setErro(e.message))
      .finally(() => vivo && setCarregando(false));
    return () => {
      vivo = false;
    };
  }, [selecionarPrimeiro]);

  return { hosts, hostId, setHostId, erro, carregando };
}

export function SeletorHost({ hosts, hostId, onMudar, incluirTodos = false }) {
  return (
    <select
      value={hostId ?? ""}
      onChange={(e) => onMudar(e.target.value === "" ? null : Number(e.target.value))}
      style={{ width: "auto", minWidth: 190 }}
    >
      {incluirTodos && <option value="">Todos os servidores</option>}
      {hosts.map((h) => (
        <option key={h.id} value={h.id} disabled={!h.enabled}>
          {h.name}
          {h.enabled ? "" : " (desativado)"}
        </option>
      ))}
    </select>
  );
}

/** Selo colorido de status usado em backups, serviços e execuções. */
export function Selo({ status }) {
  const mapa = {
    sucesso: ["pill-ok", "Sucesso"],
    executando: ["pill-info", "Executando"],
    pendente: ["pill-idle", "Na fila"],
    falha: ["pill-err", "Falha"],
    cancelado: ["pill-idle", "Cancelado"],
    running: ["pill-ok", "Rodando"],
    exited: ["pill-err", "Parado"],
    created: ["pill-idle", "Criado"],
    restarting: ["pill-warn", "Reiniciando"],
    paused: ["pill-warn", "Pausado"],
    dead: ["pill-err", "Morto"],
    healthy: ["pill-ok", "Saudável"],
    unhealthy: ["pill-err", "Com problema"],
    starting: ["pill-warn", "Subindo"],
  };
  const [classe, texto] = mapa[status] || ["pill-idle", status || "—"];
  return <span className={`pill ${classe}`}>{texto}</span>;
}

/**
 * Confirmação por digitação, para ações destrutivas.
 *
 * Um "tem certeza?" com botão OK vira reflexo depois da terceira vez.
 * Digitar o nome do servidor obriga a olhar QUAL servidor vai sofrer.
 */
export function ConfirmarDigitando({ titulo, aviso, palavra, rotuloBotao, onConfirmar, onFechar }) {
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState("");

  async function confirmar() {
    setEnviando(true);
    setErro("");
    try {
      await onConfirmar(texto);
      onFechar();
    } catch (ex) {
      setErro(ex.message);
      setEnviando(false);
    }
  }

  return (
    <div className="modal-bg" onClick={onFechar}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 520 }}>
        <div className="modal-head">
          <div className="modal-title">{titulo}</div>
        </div>
        <div className="modal-body">
          <div
            className="card card-tight"
            style={{ background: "var(--red-bg)", borderColor: "#f3b6b6", marginBottom: 16 }}
          >
            <div className="stack-h" style={{ color: "#8c1c1c", alignItems: "flex-start" }}>
              <IconAlerta size={18} />
              <div style={{ flex: 1, fontSize: 13 }}>{aviso}</div>
            </div>
          </div>
          {erro && <div className="login-err">{erro}</div>}
          <div className="field">
            <label className="label">
              Digite <strong className="mono">{palavra}</strong> para confirmar
            </label>
            <input value={texto} onChange={(e) => setTexto(e.target.value)} autoFocus />
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn btn-secondary" onClick={onFechar}>Cancelar</button>
          <button
            className="btn btn-danger"
            disabled={texto !== palavra || enviando}
            onClick={confirmar}
          >
            {enviando ? "Executando…" : rotuloBotao}
          </button>
        </div>
      </div>
    </div>
  );
}
