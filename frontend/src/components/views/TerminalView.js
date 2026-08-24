import React, { useCallback, useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import { api } from "../../api";
import { Carregando, Erro, SeletorHost, Vazio, useHosts } from "../Comuns";
import { IconTerminal } from "../Icons";

/** Tema do terminal — paleta escura DGT (mesma do Camsync). */
const TEMA = {
  background: "#0f172a",
  foreground: "#C8D3E5",
  cursor: "#3fc7e8",
  cursorAccent: "#0f172a",
  selectionBackground: "rgba(63,199,232,0.28)",
  black: "#16213a",
  red: "#ff6b6b",
  green: "#4ade80",
  yellow: "#fbbf24",
  blue: "#60a5fa",
  magenta: "#c084fc",
  cyan: "#3fc7e8",
  white: "#C8D3E5",
  brightBlack: "#475569",
  brightRed: "#ff8080",
  brightGreen: "#86efac",
  brightYellow: "#fcd34d",
  brightBlue: "#93c5fd",
  brightMagenta: "#d8b4fe",
  brightCyan: "#67e8f9",
  brightWhite: "#F1F5F9",
};

export default function TerminalView() {
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();

  const containerRef = useRef(null);
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const wsRef = useRef(null);

  const [estado, setEstado] = useState("desconectado"); // desconectado|conectando|conectado|erro
  const [erro, setErro] = useState("");
  const [info, setInfo] = useState(null);

  const desconectar = useCallback(() => {
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* já estava fechando */
      }
      wsRef.current = null;
    }
    setEstado("desconectado");
    setInfo(null);
  }, []);

  const conectar = useCallback(async () => {
    if (!hostId || !termRef.current) return;

    desconectar();
    setErro("");
    setEstado("conectando");

    const term = termRef.current;
    term.clear();
    term.writeln("\x1b[36mDGT FaceOps — InTerminal\x1b[0m");
    term.writeln("Abrindo sessão…\r\n");

    let ticket;
    try {
      const resposta = await api.ticketTerminal(hostId);
      ticket = resposta.ticket;
    } catch (ex) {
      setErro(ex.message);
      setEstado("erro");
      term.writeln(`\x1b[31m${ex.message}\x1b[0m`);
      return;
    }

    fitRef.current && fitRef.current.fit();
    const { cols, rows } = term;

    const protocolo = window.location.protocol === "https:" ? "wss" : "ws";
    const url =
      `${protocolo}://${window.location.host}/api/terminal/ws` +
      `?ticket=${encodeURIComponent(ticket)}&colunas=${cols}&linhas=${rows}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (evento) => {
      let msg;
      try {
        msg = JSON.parse(evento.data);
      } catch {
        return;
      }
      if (msg.tipo === "out") {
        term.write(msg.dados);
      } else if (msg.tipo === "pronto") {
        setEstado("conectado");
        setInfo(msg);
      } else if (msg.tipo === "erro") {
        setErro(msg.mensagem);
        setEstado("erro");
        term.writeln(`\r\n\x1b[31m${msg.mensagem}\x1b[0m`);
      } else if (msg.tipo === "fim") {
        term.writeln(`\r\n\x1b[33m— sessão encerrada (${msg.motivo}) —\x1b[0m`);
        setEstado("desconectado");
        setInfo(null);
      }
    };

    ws.onerror = () => {
      setErro("Falha na conexão do WebSocket com o painel.");
      setEstado("erro");
    };

    ws.onclose = () => {
      if (wsRef.current === ws) wsRef.current = null;
      setEstado((atual) => (atual === "erro" ? "erro" : "desconectado"));
    };
  }, [hostId, desconectar]);

  // Cria o terminal uma única vez. Recriar a cada render perderia o
  // histórico de rolagem e a posição do cursor a cada mudança de estado.
  useEffect(() => {
    if (!containerRef.current || termRef.current) return;

    const term = new Terminal({
      theme: TEMA,
      fontFamily: 'ui-monospace, "Cascadia Code", Consolas, "Courier New", monospace',
      fontSize: 13,
      lineHeight: 1.25,
      cursorBlink: true,
      scrollback: 5000,
      convertEol: false,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    fit.fit();

    term.onData((dados) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ tipo: "in", dados }));
      }
    });

    term.onResize(({ cols, rows }) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ tipo: "resize", colunas: cols, linhas: rows }));
      }
    });

    termRef.current = term;
    fitRef.current = fit;

    const aoRedimensionar = () => fit.fit();
    window.addEventListener("resize", aoRedimensionar);

    return () => {
      window.removeEventListener("resize", aoRedimensionar);
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  // Fecha a sessão ao sair da tela — PTY órfão no servidor consome
  // recurso e mantém um shell aberto sem ninguém olhando.
  useEffect(() => desconectar, [desconectar]);

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo="Cadastre um servidor primeiro" />;

  const conectado = estado === "conectado";

  return (
    <>
      <div className="page-head" style={{ marginBottom: 14 }}>
        <div>
          <div className="page-title">InTerminal</div>
          <div className="page-sub">
            Shell SSH real no servidor, pelo navegador — toda sessão é gravada
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          {conectado ? (
            <button className="btn btn-danger" onClick={desconectar}>Encerrar sessão</button>
          ) : (
            <button
              className="btn btn-primary"
              onClick={conectar}
              disabled={estado === "conectando" || !hostId}
            >
              <IconTerminal size={15} />
              {estado === "conectando" ? "Conectando…" : "Abrir terminal"}
            </button>
          )}
        </div>
      </div>

      <Erro mensagem={erro} />

      <div className="term-shell">
        <div className="term-bar">
          <IconTerminal size={15} />
          {info ? (
            <span className="mono">
              {info.usuario_ssh}@{info.host}
            </span>
          ) : (
            <span className="muted">sem sessão</span>
          )}
          <div className="term-bar-sep" />
          {info && info.gravando && (
            <span className="term-badge term-badge-rec">gravando</span>
          )}
          {info && info.sudo && <span className="term-badge">sudo liberado</span>}
          <span className={`pill ${conectado ? "pill-ok" : estado === "erro" ? "pill-err" : "pill-idle"}`}>
            {conectado ? "conectado" : estado}
          </span>
        </div>
        <div className="term-host" ref={containerRef} />
      </div>

      <div className="small muted" style={{ marginTop: 10 }}>
        A sessão cai sozinha depois de 30 minutos parada. Tudo o que for digitado
        fica registrado em <span className="mono">.cast</span> e pode ser reproduzido
        com <span className="mono">asciinema play</span> na tela de Auditoria.
      </div>
    </>
  );
}
