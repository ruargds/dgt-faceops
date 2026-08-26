import React, { useCallback, useEffect, useRef, useState } from "react";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal } from "@xterm/xterm";
import { api } from "../../api";
import { MARCA_PADRAO } from "../../marca";
import { useSessao } from "../../usePermissions";
import { Carregando, Erro, SeletorHost, Vazio, fecharSeForaLimpo, useHosts } from "../Comuns";
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
  const { marca } = useSessao();
  const nomePainel = (marca || MARCA_PADRAO).nome;
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();

  const containerRef = useRef(null);
  const termRef = useRef(null);
  const fitRef = useRef(null);
  const wsRef = useRef(null);

  const [estado, setEstado] = useState("desconectado"); // desconectado|conectando|conectado|erro
  const [erro, setErro] = useState("");
  const [info, setInfo] = useState(null);
  const [pedindoLogin, setPedindoLogin] = useState(false);
  // Colagem manual: usada quando o navegador nega a LEITURA do clipboard
  // (o Firefox nega sempre). Sem esta saída, "colar" falharia em silêncio
  // em metade dos navegadores e pareceria defeito do painel.
  const [colando, setColando] = useState(false);

  const hostSelecionado = hosts.find((h) => h.id === hostId) || null;

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

  const conectar = useCallback(
    async (credencial) => {
      if (!hostId || !termRef.current) return;

      desconectar();
      setErro("");
      setEstado("conectando");

      const term = termRef.current;
      term.clear();
      term.writeln(`\x1b[36m${nomePainel} — InTerminal\x1b[0m`);
      term.writeln("Abrindo sessão…\r\n");

      let ticket;
      try {
        const resposta = await api.ticketTerminal(hostId, credencial);
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
          term.focus();
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
    },
    [hostId, desconectar, nomePainel]
  );

  // ── Copiar e colar, no comportamento do PuTTY ──────────────────────
  // Ctrl+C dentro de um terminal É o sinal de interrupção — quem opera
  // servidor conta com isso para matar comando travado, e sequestrar a
  // tecla para "copiar" seria pior que não ter cópia. Então: selecionar já
  // copia, clique direito cola, e Ctrl+Shift+C/V ficam como atalho
  // explícito.
  const enviar = useCallback((texto) => {
    const ws = wsRef.current;
    if (!texto || !ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify({ tipo: "in", dados: texto }));
    return true;
  }, []);

  const copiar = useCallback(() => {
    const term = termRef.current;
    const selecao = term && term.getSelection();
    if (!selecao) return;
    try {
      navigator.clipboard.writeText(selecao).catch(() => {});
    } catch {
      /* sem API de clipboard — resta o Ctrl+C do próprio navegador */
    }
  }, []);

  const colar = useCallback(async () => {
    if (!wsRef.current) return;
    try {
      const texto = await navigator.clipboard.readText();
      if (texto) {
        enviar(texto);
        termRef.current && termRef.current.focus();
        return;
      }
    } catch {
      /* leitura negada pelo navegador — cai na janela de colagem manual */
    }
    setColando(true);
  }, [enviar]);

  // Os handlers do xterm são presos por ref: o terminal é criado uma única
  // vez, e sem a ref ele carregaria para sempre a primeira versão da função.
  const colarRef = useRef(colar);
  const copiarRef = useRef(copiar);
  useEffect(() => {
    colarRef.current = colar;
    copiarRef.current = copiar;
  }, [colar, copiar]);

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
      // Clique direito é "colar", não "selecionar palavra" — é o que a mão
      // de quem vem do PuTTY espera.
      rightClickSelectsWord: false,
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

    // Selecionar já copia, como no PuTTY.
    term.onSelectionChange(() => {
      const selecao = term.getSelection();
      if (!selecao) return;
      try {
        navigator.clipboard.writeText(selecao).catch(() => {});
      } catch {
        /* sem permissão: a seleção continua lá para o Ctrl+C do navegador */
      }
    });

    term.attachCustomKeyEventHandler((evento) => {
      if (evento.type !== "keydown") return true;
      const tecla = (evento.key || "").toLowerCase();
      if (evento.ctrlKey && evento.shiftKey && tecla === "c") {
        copiarRef.current();
        return false;
      }
      if (evento.ctrlKey && evento.shiftKey && tecla === "v") {
        colarRef.current();
        return false;
      }
      // Ctrl+Insert / Shift+Insert — o par que o PuTTY usa há vinte anos.
      if (evento.key === "Insert" && evento.ctrlKey) {
        copiarRef.current();
        return false;
      }
      if (evento.key === "Insert" && evento.shiftKey) {
        colarRef.current();
        return false;
      }
      return true;
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
            <>
              <button className="btn btn-secondary" onClick={colar}>
                Colar
              </button>
              <button className="btn btn-danger" onClick={desconectar}>
                Encerrar sessão
              </button>
            </>
          ) : (
            <button
              className="btn btn-primary"
              onClick={() => setPedindoLogin(true)}
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
        <div
          className="term-host"
          ref={containerRef}
          onContextMenu={(e) => {
            e.preventDefault();
            colar();
          }}
        />
      </div>

      <div className="small muted" style={{ marginTop: 10 }}>
        Selecionar já copia; clique direito cola (ou{" "}
        <span className="mono">Ctrl+Shift+V</span>).{" "}
        <span className="mono">Ctrl+C</span> continua interrompendo o comando,
        como em qualquer terminal.
        <br />
        A sessão cai sozinha depois de 30 minutos parada. Tudo o que for digitado
        fica registrado em <span className="mono">.cast</span> e pode ser reproduzido
        com <span className="mono">asciinema play</span> na tela de Auditoria.
      </div>

      {pedindoLogin && (
        <ModalLoginSsh
          host={hostSelecionado}
          onFechar={() => setPedindoLogin(false)}
          onEntrar={(credencial) => {
            setPedindoLogin(false);
            conectar(credencial);
          }}
        />
      )}

      {colando && (
        <ModalColar
          onFechar={() => setColando(false)}
          onEnviar={(texto) => {
            setColando(false);
            enviar(texto);
            termRef.current && termRef.current.focus();
          }}
        />
      )}
    </>
  );
}

/**
 * Login da sessão.
 *
 * Existe para que a pessoa entre no servidor com a conta dela, como faria no
 * PuTTY, em vez de herdar a conta de serviço guardada no cofre. A senha não é
 * gravada em lugar nenhum: vai no corpo do pedido de ticket, vive 30 segundos
 * em memória do painel e é descartada quando o SSH autentica.
 */
function ModalLoginSsh({ host, onFechar, onEntrar }) {
  const [usuario, setUsuario] = useState((host && host.ssh_user) || "");
  const [senha, setSenha] = useState("");

  const temCofre = Boolean(host && host.tem_credencial);

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <form
        className="modal"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          onEntrar({ usuario: usuario.trim(), senha });
        }}
      >
        <div className="modal-head">
          <div className="modal-title">
            Entrar em {host ? host.name : "servidor"}
          </div>
        </div>
        <div className="modal-body">
          <div className="field">
            <label className="label label-required">Usuário</label>
            <input
              value={usuario}
              onChange={(e) => setUsuario(e.target.value)}
              autoComplete="off"
              required
              autoFocus
            />
            <div className="field-help">
              {host ? `${host.address}:${host.ssh_port}` : "—"}
            </div>
          </div>
          <div className="field">
            <label className="label">Senha</label>
            <input
              type="password"
              value={senha}
              onChange={(e) => setSenha(e.target.value)}
              autoComplete="new-password"
              placeholder={temCofre ? "em branco = credencial do cofre" : ""}
            />
            <div className="field-help">
              {temCofre
                ? "Em branco usa a credencial cadastrada do servidor — é o caminho de quem entra por chave PEM."
                : "Este servidor não tem credencial no cofre; a senha é obrigatória aqui."}
            </div>
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>
            Cancelar
          </button>
          <button className="btn btn-primary" disabled={!usuario.trim()}>
            <IconTerminal size={15} /> Conectar
          </button>
        </div>
      </form>
    </div>
  );
}

/**
 * Colagem manual.
 *
 * O Chrome libera a LEITURA do clipboard por script depois de um gesto; o
 * Firefox não libera. Em vez de deixar "colar" falhar em silêncio, o texto
 * entra aqui com o Ctrl+V do próprio navegador e segue para o shell.
 */
function ModalColar({ onFechar, onEnviar }) {
  const [texto, setTexto] = useState("");

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <form
        className="modal"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          onEnviar(texto);
        }}
      >
        <div className="modal-head">
          <div className="modal-title">Colar no terminal</div>
        </div>
        <div className="modal-body">
          <div className="field">
            <label className="label">Conteúdo</label>
            <textarea
              className="mono"
              rows={6}
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              autoFocus
            />
            <div className="field-help">
              Cole aqui com Ctrl+V. O texto vai para o shell exatamente como
              está — se terminar com quebra de linha, o comando executa.
            </div>
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>
            Cancelar
          </button>
          <button className="btn btn-primary" disabled={!texto}>
            Enviar
          </button>
        </div>
      </form>
    </div>
  );
}
