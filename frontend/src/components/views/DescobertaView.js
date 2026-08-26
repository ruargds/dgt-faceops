import React, { useCallback, useEffect, useRef, useState } from "react";
import { api, formatBytes } from "../../api";
import { t } from "../../i18n";
import { Carregando, Erro, SeletorHost, Vazio, useHosts } from "../Comuns";
import { IconServidor } from "../Icons";

/**
 * Descoberta — inventário do que roda em cada servidor.
 *
 * Sob demanda: uma sondagem por SSH, nunca no coletor contínuo. Responde
 * "onde está o banco?", "tem GPU?", "que portas estão abertas?" — as
 * mesmas perguntas que a topologia foi levantada respondendo na mão.
 * Serve igual para FindFace distribuído e para tudo num servidor só.
 */
export default function DescobertaView() {
  const { hosts, hostId, setHostId, erro: erroHosts, carregando: carregandoHosts } = useHosts();
  const [dados, setDados] = useState(null);
  const [carregando, setCarregando] = useState(false);
  const [erro, setErro] = useState("");
  // Cache por servidor: trocar de host mostra na hora o que já foi
  // sondado, e o auto-scan não repete a varredura pesada a cada visita.
  const cache = useRef({});
  const pedido = useRef(0);

  const inventariar = useCallback(async (id, { forcar = false } = {}) => {
    if (!id) return;
    if (!forcar && cache.current[id]) {
      setDados(cache.current[id]);
      setErro("");
      return;
    }
    const meu = ++pedido.current;
    setErro("");
    setCarregando(true);
    setDados(null);
    try {
      const r = await api.descoberta(id);
      cache.current[id] = r;
      if (meu === pedido.current) setDados(r);
    } catch (ex) {
      if (meu === pedido.current) setErro(ex.message);
    } finally {
      if (meu === pedido.current) setCarregando(false);
    }
  }, []);

  // Primeira busca automática ao abrir a tela e a cada troca de servidor —
  // o operador chega e já vê o mapa da máquina, sem precisar pedir.
  useEffect(() => {
    if (hostId) inventariar(hostId);
  }, [hostId, inventariar]);

  if (carregandoHosts) return <Carregando />;
  if (erroHosts) return <Erro mensagem={erroHosts} />;
  if (!hosts.length) return <Vazio titulo="Cadastre um servidor primeiro" />;

  return (
    <>
      <div className="page-head" style={{ marginBottom: 14 }}>
        <div>
          <div className="page-title">{t("tela.descoberta")}</div>
          <div className="page-sub">
            {t("tela.descoberta.sub")}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <button
            className="btn btn-primary"
            onClick={() => inventariar(hostId, { forcar: true })}
            disabled={carregando || !hostId}
          >
            <IconServidor size={15} />
            {carregando ? "Sondando…" : dados ? "Atualizar" : "Inventariar"}
          </button>
        </div>
      </div>

      <Erro mensagem={erro} />

      {carregando && (
        <Carregando texto="Sondando o servidor por SSH — containers, bancos, portas, disco…" />
      )}

      {!carregando && !dados && !erro && (
        <Vazio titulo="Selecione um servidor">
          A varredura roda sozinha ao escolher o servidor. É uma sondagem
          única por SSH — não entra no monitor contínuo.
        </Vazio>
      )}

      {dados && <Inventario d={dados} />}
    </>
  );
}

const GRID2 = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 16 };
const CHIP = {
  display: "inline-block",
  padding: "2px 8px",
  borderRadius: 6,
  background: "var(--bg-2)",
  border: "1px solid var(--border)",
  fontSize: 12,
};

function Inventario({ d }) {
  return (
    <div className="stack-v" style={{ gap: 16 }}>
      <ResumoServidor d={d} />
      {d.cloudflared && d.cloudflared.instalado && (
        <Cloudflared cf={d.cloudflared} hostId={d.host_id} />
      )}
      <BancosDados servicos={d.servicos_dados} />
      <Containers containers={d.containers} projetos={d.projetos} />
      <div style={GRID2}>
        <Portas portas={d.portas_ouvindo} />
        <Discos discos={d.discos} />
      </div>
    </div>
  );
}

function Linha({ rotulo, valor }) {
  if (valor === null || valor === undefined || valor === "") return null;
  return (
    <div className="stack-h" style={{ justifyContent: "space-between", gap: 12 }}>
      <span className="small muted">{rotulo}</span>
      <span className="small" style={{ fontWeight: 500, textAlign: "right" }}>{valor}</span>
    </div>
  );
}

function ResumoServidor({ d }) {
  const ff = d.findface || {};
  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 10 }}>
        {d.host} <span className="small muted">— {d.papel || "servidor"}</span>
      </div>
      <div style={GRID2}>
        <div className="stack-v" style={{ gap: 4 }}>
          <Linha rotulo="Endereço" valor={d.endereco} />
          <Linha rotulo="Sistema" valor={d.so} />
          <Linha rotulo="Kernel" valor={d.kernel} />
          <Linha rotulo="No ar há" valor={d.uptime} />
        </div>
        <div className="stack-v" style={{ gap: 4 }}>
          <Linha
            rotulo="CPU"
            valor={d.cpu_modelo ? `${d.cpu_modelo} (${d.cpus} núcleos)` : `${d.cpus} núcleos`}
          />
          <Linha rotulo="Memória" valor={d.memoria_total_bytes ? formatBytes(d.memoria_total_bytes) : "—"} />
          <Linha
            rotulo="GPU"
            valor={d.gpus && d.gpus.length ? d.gpus.map((g) => `${g.nome} (${g.memoria})`).join(", ") : "nenhuma"}
          />
          <Linha rotulo="Docker" valor={d.docker.versao || "—"} />
          <Linha rotulo="Compose" valor={d.docker.compose || "—"} />
        </div>
      </div>
      <div className="stack-h" style={{ gap: 8, marginTop: 12, flexWrap: "wrap" }}>
        <span className={`pill ${d.docker.rodando ? "pill-ok" : "pill-warn"}`}>
          {d.docker.rodando}/{d.docker.total_containers} containers de pé
        </span>
        {ff.presente && <span className="pill pill-ok">FindFace aqui ({ff.containers})</span>}
        {ff.tem_banco && <span className="pill pill-ok">banco aqui</span>}
        {ff.tem_tarantool && <span className="pill pill-ok">vetores (Tarantool) aqui</span>}
        {!ff.presente && <span className="pill pill-warn">FindFace não roda neste servidor</span>}
      </div>
    </div>
  );
}

function BancosDados({ servicos }) {
  const bancos = servicos || [];
  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 8 }}>
        Serviços de dados <span className="small muted">— onde moram câmeras, usuários e vetores</span>
      </div>
      {bancos.length === 0 ? (
        <div className="small muted">
          Nenhum banco encontrado neste servidor. Numa instalação distribuída
          isso é esperado — o banco fica em outro servidor; rode a descoberta lá.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Tipo</th>
                <th>Para que serve</th>
                <th>Container</th>
                <th>Estado</th>
                <th>Portas</th>
              </tr>
            </thead>
            <tbody>
              {bancos.map((b, i) => (
                <tr key={i}>
                  <td className="mono">{b.tipo}</td>
                  <td className="small">{b.rotulo}</td>
                  <td className="mono small">{b.container}</td>
                  <td>
                    <span className={`pill ${b.estado === "running" ? "pill-ok" : "pill-err"}`}>
                      {b.estado}
                    </span>
                  </td>
                  <td className="mono small">{b.portas || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Containers({ containers, projetos }) {
  const [aberto, setAberto] = useState(false);
  const lista = aberto ? containers : containers.slice(0, 12);
  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 8 }}>
        Containers ({containers.length}){" "}
        <span className="small muted">
          — {projetos.map((p) => `${p.projeto} (${p.containers})`).join("  ·  ") || "sem projeto compose"}
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Serviço</th>
              <th>Imagem</th>
              <th>Estado</th>
              <th>Status</th>
              <th>Portas</th>
            </tr>
          </thead>
          <tbody>
            {lista.map((c, i) => (
              <tr key={i}>
                <td className="mono small">{c.servico}</td>
                <td
                  className="mono small"
                  style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                  title={c.imagem}
                >
                  {c.imagem}
                </td>
                <td>
                  <span className={`pill ${c.estado === "running" ? "pill-ok" : "pill-warn"}`}>
                    {c.estado}
                  </span>
                </td>
                <td className="small muted">{c.status}</td>
                <td className="mono small">{c.portas || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {containers.length > 12 && (
        <button className="btn btn-ghost btn-sm" style={{ marginTop: 8 }} onClick={() => setAberto((v) => !v)}>
          {aberto ? "Mostrar menos" : `Mostrar todos os ${containers.length}`}
        </button>
      )}
    </div>
  );
}

function Portas({ portas }) {
  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 8 }}>
        Portas ouvindo ({portas.length})
      </div>
      {portas.length === 0 ? (
        <div className="small muted">Nenhuma porta TCP em LISTEN detectada.</div>
      ) : (
        <div className="stack-h" style={{ flexWrap: "wrap", gap: 6 }}>
          {portas.map((p, i) => (
            <span key={i} className="mono" style={CHIP} title={p.processo}>
              {p.porta}
              {p.processo ? ` · ${p.processo}` : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function Cloudflared({ cf, hostId }) {
  const [reiniciando, setReiniciando] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [erro, setErro] = React.useState("");

  const ativo = cf.ativo;
  const comoRoda =
    cf.modo === "systemd"
      ? "serviço do sistema (systemd)"
      : cf.modo === "docker"
      ? `container Docker${cf.docker.container ? ` (${cf.docker.container})` : ""}`
      : "modo desconhecido";

  async function reiniciar() {
    setErro("");
    setMsg("");
    setReiniciando(true);
    try {
      const r = await api.reiniciarCloudflared(hostId);
      setMsg(`Reiniciado (${r.estado || "ok"}).`);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setReiniciando(false);
    }
  }

  return (
    <div className="card">
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <div className="section-title" style={{ marginBottom: 0 }}>
          Cloudflare Tunnel <span className="small muted">— publica o painel para fora</span>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={reiniciar} disabled={reiniciando}>
          {reiniciando ? "Reiniciando…" : "Reiniciar túnel"}
        </button>
      </div>
      <div className="stack-h" style={{ gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
        <span className={`pill ${ativo ? "pill-ok" : "pill-warn"}`}>
          {ativo ? "ativo" : "parado"}
        </span>
        <span className="mono" style={CHIP}>{comoRoda}</span>
        {cf.versao && <span className="mono" style={CHIP}>{cf.versao}</span>}
        {cf.systemd.presente && (
          <span className="mono" style={CHIP}>
            systemd: {cf.systemd.habilitado ? "habilitado no boot" : "não habilitado no boot"}
          </span>
        )}
      </div>
      {msg && <div className="small" style={{ color: "var(--green)" }}>{msg}</div>}
      {erro && <div className="small" style={{ color: "var(--red)" }}>{erro}</div>}
      <div className="small muted" style={{ marginTop: 6 }}>
        Reiniciar o túnel derruba o acesso externo por alguns segundos e o
        restabelece. O acesso interno pela rede não é afetado.
      </div>
    </div>
  );
}

function Discos({ discos }) {
  const nivel = (p) => (p >= 90 ? "err" : p >= 75 ? "warn" : "ok");
  return (
    <div className="card">
      <div className="section-title" style={{ marginBottom: 8 }}>Discos</div>
      <div className="stack-v" style={{ gap: 10 }}>
        {discos.map((d, i) => (
          <div key={i}>
            <div className="stack-h" style={{ justifyContent: "space-between" }}>
              <span className="mono small">{d.ponto}</span>
              <span className="small muted">
                {formatBytes(d.livre_bytes)} livres de {formatBytes(d.total_bytes)}
              </span>
            </div>
            <div className="meter" style={{ marginTop: 4 }}>
              <div className={`meter-fill meter-${nivel(d.percentual)}`} style={{ width: `${d.percentual}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
