import React, { useEffect, useState } from "react";
import { api, nivel } from "../api";
import { t } from "../i18n";
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
            {t("comum.tentar")}
          </button>
        )}
      </div>
    </div>
  );
}

export function Carregando({ texto }) {
  const rotulo = texto || t("comum.carregando");
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
          {h.rotulo || h.name}
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
      <div className="small" style={{ color: "var(--amber)" }}>{t("Nenhum destino ativo. Cadastre um em")} <strong>{t("Destinos")}</strong> {t("antes de continuar.")}</div>
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
            <label className="label">{t("Digite")} <strong className="mono">{palavra}</strong> {t("para confirmar")}</label>
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

// ── Seletor de período ─────────────────────────────────────────────────
// O controle de tempo que todo painel de série tem, e que faltava aqui:
// atalhos de janela, navegação para trás e para a frente, e intervalo
// absoluto quando a pergunta é "o que houve na madrugada de terça".
//
// Uma decisão de honestidade no meio: atalho que pede mais tempo do que o
// banco guarda aparece MARCADO, com o prazo real no title. Oferecer "1
// ano" e desenhar sete dias faria a tela mentir por omissão — e a
// retenção curta da série por container é escolha de custo, não defeito.

const ATALHOS = [
  { rotulo: "1h", horas: 1 },
  { rotulo: "6h", horas: 6 },
  { rotulo: "12h", horas: 12 },
  { rotulo: "24h", horas: 24 },
  { rotulo: "2d", horas: 48 },
  { rotulo: "7d", horas: 168 },
  { rotulo: "30d", horas: 720 },
  { rotulo: "90d", horas: 2160 },
  { rotulo: "6m", horas: 4380 },
  { rotulo: "1a", horas: 8760 },
];

/** ISO → valor aceito por `<input type="datetime-local">` (hora local). */
function paraCampo(iso) {
  const d = iso ? new Date(iso) : new Date();
  const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

/** O par (início, fim) que o período representa agora. */
export function intervaloDe(periodo) {
  if (periodo && periodo.de && periodo.ate) {
    return { de: new Date(periodo.de), ate: new Date(periodo.ate) };
  }
  const horas = (periodo && periodo.horas) || 6;
  const ate = new Date();
  return { de: new Date(ate.getTime() - horas * 3600e3), ate };
}

export function SeletorPeriodo({
  valor,
  onMudar,
  disponivelDesde = null,
  retencaoDias = null,
  rotuloDado = "histórico",
}) {
  const [aberto, setAberto] = React.useState(false);
  const { de, ate } = intervaloDe(valor);
  const [campoDe, setCampoDe] = React.useState(paraCampo(de.toISOString()));
  const [campoAte, setCampoAte] = React.useState(paraCampo(ate.toISOString()));

  // Os campos partem do período que está na tela — sincronizados no
  // CLIQUE que abre o painel, não num efeito. Num período relativo o
  // "até" é `new Date()`, que muda a cada render: um efeito que
  // dependesse dele gravaria estado a cada render e giraria em laço.
  const abrirPainel = () => {
    const proximo = !aberto;
    if (proximo) {
      setCampoDe(paraCampo(de.toISOString()));
      setCampoAte(paraCampo(ate.toISOString()));
    }
    setAberto(proximo);
  };

  const desde = disponivelDesde ? new Date(disponivelDesde) : null;
  const relativo = !(valor && valor.de);
  const larguraMs = ate - de;

  // Andar no tempo mantém o TAMANHO da janela e vira intervalo absoluto:
  // "as 6 horas anteriores a estas 6 horas" só faz sentido com âncora.
  const andar = (sinal) => {
    const novoDe = new Date(de.getTime() + sinal * larguraMs);
    const novoAte = new Date(ate.getTime() + sinal * larguraMs);
    const agora = new Date();
    if (novoAte >= agora) return onMudar({ horas: larguraMs / 3600e3 });
    onMudar({ de: novoDe.toISOString(), ate: novoAte.toISOString() });
  };

  const aplicar = () => {
    if (!campoDe || !campoAte) return;
    const d = new Date(campoDe);
    const a = new Date(campoAte);
    if (!(d < a)) return;
    onMudar({ de: d.toISOString(), ate: a.toISOString() });
    setAberto(false);
  };

  return (
    <div className="stack-h" style={{ gap: 4, flexWrap: "wrap", alignItems: "center" }}>
      <button
        className="btn btn-ghost btn-sm"
        onClick={() => andar(-1)}
        title="Janela anterior, do mesmo tamanho"
        aria-label="Período anterior"
      >
        ‹
      </button>

      {ATALHOS.map((a) => {
        const alemDoDado =
          desde && new Date(Date.now() - a.horas * 3600e3) < desde;
        return (
          <button
            key={a.horas}
            className={`btn btn-sm ${
              relativo && valor.horas === a.horas ? "btn-primary" : "btn-ghost"
            }`}
            onClick={() => onMudar({ horas: a.horas })}
            style={alemDoDado ? { opacity: 0.55 } : undefined}
            title={
              alemDoDado
                ? `O ${rotuloDado} começa em ${desde.toLocaleString("pt-BR")}` +
                  (retencaoDias ? ` (retenção de ${retencaoDias} dias)` : "") +
                  " — o gráfico mostra até onde há dado."
                : `Últimas ${a.rotulo}`
            }
          >
            {a.rotulo}
            {alemDoDado ? "·" : ""}
          </button>
        );
      })}

      <button
        className="btn btn-ghost btn-sm"
        onClick={() => andar(1)}
        disabled={relativo}
        title="Janela seguinte"
        aria-label="Período seguinte"
      >
        ›
      </button>

      <button
        className={`btn btn-sm ${aberto ? "btn-primary" : "btn-ghost"}`}
        onClick={abrirPainel}
        title="Escolher início e fim exatos"
      >
        {relativo ? "Período…" : "Período fixo"}
      </button>

      {!relativo && (
        <span className="small muted">
          {de.toLocaleString("pt-BR")} → {ate.toLocaleString("pt-BR")}
        </span>
      )}

      {aberto && (
        <div
          className="card card-tight"
          style={{
            display: "flex",
            gap: 8,
            alignItems: "flex-end",
            flexWrap: "wrap",
            width: "100%",
            marginTop: 6,
          }}
        >
          <label className="small">
            De
            <input
              type="datetime-local"
              value={campoDe}
              onChange={(e) => setCampoDe(e.target.value)}
            />
          </label>
          <label className="small">
            Até
            <input
              type="datetime-local"
              value={campoAte}
              onChange={(e) => setCampoAte(e.target.value)}
            />
          </label>
          <button className="btn btn-primary btn-sm" onClick={aplicar}>
            Aplicar
          </button>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => {
              onMudar({ horas: 6 });
              setAberto(false);
            }}
          >
            Voltar para 6h
          </button>
          {desde && (
            <span className="small muted">
              Há {rotuloDado} desde {desde.toLocaleString("pt-BR")}
              {retencaoDias ? ` · retenção de ${retencaoDias} dias` : ""}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
