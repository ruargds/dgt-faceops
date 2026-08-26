import React, { useEffect, useState } from "react";
import { api, nivel } from "../api";
import { t as traduzir } from "../i18n";
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
    <div className="card" style={{ borderColor: "var(--red-bd)", background: "var(--red-bg)" }}>
      <div className="stack-h" style={{ color: "var(--red-fg)" }}>
        <IconAlerta size={18} />
        <div style={{ flex: 1, fontSize: 13 }}>{mensagem}</div>
        {onTentar && (
          <button className="btn btn-secondary btn-sm" onClick={onTentar}>
            {traduzir("comum.tentar")}
          </button>
        )}
      </div>
    </div>
  );
}

export function Carregando({ texto }) {
  const rotulo = texto || traduzir("comum.carregando");
  return (
    <div className="stack-h" style={{ padding: 18, color: "var(--text-3)" }}>
      <div className="spin" /> {rotulo}
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
      {incluirTodos && <option value="">{t("Todos os servidores")}</option>}
      {hosts.map((h) => (
        <option key={h.id} value={h.id} disabled={!h.enabled}>
          {h.name}
          {h.enabled ? "" : " (desativado)"}
        </option>
      ))}
    </select>
  );
}

/**
 * Destinos cadastrados. Os marcados como padrão vêm pré-selecionados —
 * quem só quer disparar um backup não deveria precisar escolher nada.
 */
export function useDestinos() {
  const [destinos, setDestinos] = useState([]);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);

  useEffect(() => {
    let vivo = true;
    api
      .destinos()
      .then((l) => vivo && setDestinos(l))
      .catch((e) => vivo && setErro(e.message))
      .finally(() => vivo && setCarregando(false));
    return () => {
      vivo = false;
    };
  }, []);

  const ativos = destinos.filter((d) => d.enabled);
  const padroes = ativos.filter((d) => d.padrao).map((d) => d.id);
  const nomePorId = Object.fromEntries(destinos.map((d) => [d.id, d.nome]));

  return { destinos, ativos, padroes, nomePorId, erro, carregando };
}

/** Caixas de seleção de destino, compartilhadas por backup e agendamento. */
export function SeletorDestinos({ destinos, selecionados, onMudar }) {
  if (!destinos.length) {
    return (
      <div className="small" style={{ color: "var(--amber)" }}>{t("Nenhum destino ativo. Cadastre um em")}<strong>{t("Destinos")}</strong>{t("antes de continuar.")}</div>
    );
  }
  return (
    <>
      {destinos.map((d) => (
        <label className="check" key={d.id}>
          <input
            type="checkbox"
            checked={selecionados.includes(d.id)}
            onChange={() =>
              onMudar(
                selecionados.includes(d.id)
                  ? selecionados.filter((x) => x !== d.id)
                  : [...selecionados, d.id]
              )
            }
          />
          <span>
            {d.nome}{" "}
            <span className="muted">— {d.tipo}</span>
            {d.last_test_at && !d.last_test_ok && (
              <span className="pill pill-err" style={{ marginLeft: 6 }}>
                último teste falhou
              </span>
            )}
          </span>
        </label>
      ))}
    </>
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
 * Props para o fundo de janela flutuante fechar só por gesto deliberado.
 *
 * Uso: <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
 *
 * Fecha apenas se o clique COMEÇOU e TERMINOU no próprio fundo. Sem isso,
 * arrastar uma seleção de texto de dentro do modal para fora fecha a
 * janela — o mouseup cai no fundo e dispara o onClick. O gesto de fechar
 * continua natural (clique limpo no fundo), mas arrastar não fecha mais.
 */
export function fecharSeForaLimpo(onFechar) {
  return {
    onMouseDown: (e) => {
      e.currentTarget.dataset.iniciouNoFundo =
        e.target === e.currentTarget ? "1" : "0";
    },
    onClick: (e) => {
      if (e.target === e.currentTarget && e.currentTarget.dataset.iniciouNoFundo === "1") {
        onFechar();
      }
      e.currentTarget.dataset.iniciouNoFundo = "0";
    },
  };
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
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 520 }}>
        <div className="modal-head">
          <div className="modal-title">{titulo}</div>
        </div>
        <div className="modal-body">
          <div
            className="card card-tight"
            style={{ background: "var(--red-bg)", borderColor: "var(--red-bd)", marginBottom: 16 }}
          >
            <div className="stack-h" style={{ color: "var(--red-fg)", alignItems: "flex-start" }}>
              <IconAlerta size={18} />
              <div style={{ flex: 1, fontSize: 13 }}>{aviso}</div>
            </div>
          </div>
          {erro && <div className="login-err">{erro}</div>}
          <div className="field">
            <label className="label">{t("Digite")}<strong className="mono">{palavra}</strong>{t("para confirmar")}</label>
            <input value={texto} onChange={(e) => setTexto(e.target.value)} autoFocus />
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn btn-secondary" onClick={onFechar}>{t("Cancelar")}</button>
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
