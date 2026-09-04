import React, { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { t } from "../../i18n";
import { Carregando, Erro, Vazio } from "../Comuns";
import { IconAtualizar, IconGPU } from "../Icons";

/**
 * Topologia — o Face Detect distribuído visto como cadeia de dependências.
 *
 * O fornecedor reparte os componentes entre máquinas para balancear
 * carga: GPU numa, Tarantool noutra, Postgres noutra, app noutra. Esta
 * tela varre todos os servidores e desenha o fluxo — câmera → vídeo →
 * extração → busca → vetores/dados → app — dizendo em QUAL servidor cada
 * camada roda. Cada servidor tem uma cor; segui-la pelas camadas mostra
 * o que aquela máquina carrega.
 */

// Paleta estável por índice de servidor. Cores distinguíveis também para
// quem não separa bem verde de vermelho (matizes e claridades variadas).
const CORES = [
  "#1a6fc4", "#0d9488", "#b45309", "#7c3aed",
  "#be123c", "#4d7c0f", "#0369a1", "#a21caf",
];

export default function TopologiaView() {
  const [dados, setDados] = useState(null);
  const [carregando, setCarregando] = useState(true);
  const [erro, setErro] = useState("");
  const pedido = useRef(0);

  const carregar = useCallback(async () => {
    const meu = ++pedido.current;
    setErro("");
    setCarregando(true);
    try {
      const r = await api.topologia();
      if (meu === pedido.current) setDados(r);
    } catch (ex) {
      if (meu === pedido.current) setErro(ex.message);
    } finally {
      if (meu === pedido.current) setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  const corDe = (hostId) => {
    if (!dados) return CORES[0];
    const i = dados.servidores.findIndex((s) => s.host_id === hostId);
    return CORES[(i < 0 ? 0 : i) % CORES.length];
  };
  const nomeDe = (hostId) => {
    const s = dados && dados.servidores.find((x) => x.host_id === hostId);
    return s ? s.host : `#${hostId}`;
  };

  return (
    <>
      <div className="page-head" style={{ marginBottom: 14 }}>
        <div>
          <div className="page-title">{t("tela.topologia")}</div>
          <div className="page-sub">
            {t("tela.topologia.sub")}
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={carregar} disabled={carregando}>
            <IconAtualizar size={15} /> {carregando ? "Varrendo…" : "Atualizar"}
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {carregando && !dados && (
        <Carregando texto={t("Varrendo os servidores por SSH e montando o mapa…")} />
      )}

      {dados && dados.servidores.length === 0 && (
        <Vazio titulo={t("Nenhum servidor habilitado")}>
          Cadastre e habilite servidores para o painel montar a topologia.
        </Vazio>
      )}

      {dados && dados.servidores.length > 0 && (
        <>
          <div
            className="card card-tight"
            style={{ marginBottom: 14, background: "var(--bg-2)" }}
          >
            <span className="small">
              {dados.distribuido ? (
                <>{t("Instalação")} <strong>{t("distribuída")}</strong>: o processamento está
                  repartido entre {dados.servidores.filter((s) => s.camadas.length).length}{" "}
                  servidores. Cada coluna abaixo é uma etapa do reconhecimento; os
                  blocos coloridos dizem em que máquina ela roda.
                </>
              ) : (
                <>{t("Instalação")} <strong>concentrada</strong>: as etapas rodam
                  praticamente num servidor só. O mesmo mapa vale — só que numa
                  coluna de cor única.
                </>
              )}
            </span>
          </div>

          <Fluxo dados={dados} corDe={corDe} nomeDe={nomeDe} />

          <Legenda dados={dados} corDe={corDe} />
        </>
      )}
    </>
  );
}

function Fluxo({ dados, corDe, nomeDe }) {
  return (
    <div style={{ overflowX: "auto", paddingBottom: 8 }}>
      <div style={{ display: "flex", alignItems: "stretch", gap: 0, minWidth: "min-content" }}>
        {dados.camadas.map((camada, i) => (
          <React.Fragment key={camada.chave}>
            <ColunaCamada
              camada={camada}
              hosts={dados.camada_hosts[camada.chave] || []}
              corDe={corDe}
              nomeDe={nomeDe}
            />
            {i < dados.camadas.length - 1 && <Seta />}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

function Seta() {
  return (
    <div
      aria-hidden
      style={{
        display: "flex",
        alignItems: "center",
        color: "var(--text-3)",
        fontSize: 22,
        padding: "0 6px",
        flex: "0 0 auto",
      }}
    >
      ›
    </div>
  );
}

function ColunaCamada({ camada, hosts, corDe, nomeDe }) {
  const externa = camada.externo;
  return (
    <div
      className="card card-tight"
      style={{
        flex: "0 0 190px",
        width: 190,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        borderStyle: externa ? "dashed" : "solid",
      }}
    >
      <div>
        <div className="stack-h" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <strong style={{ fontSize: 13.5 }}>{camada.nome}</strong>
          {camada.gpu && (
            <span className="pill pill-info" title={t("Usa GPU")}>
              <IconGPU size={11} /> {t("GPU")}</span>
          )}
        </div>
        <div className="small muted" style={{ marginTop: 2, minHeight: 30 }}>
          {camada.desc}
        </div>
      </div>

      <div className="stack-v" style={{ gap: 5 }}>
        {externa ? (
          <span className="small muted" style={{ fontStyle: "italic" }}>
            origem externa (dispositivos)
          </span>
        ) : hosts.length === 0 ? (
          <span className="small" style={{ color: "var(--amber)" }}>{t("não detectada")}</span>
        ) : (
          hosts.map((hid) => (
            <span
              key={hid}
              title={nomeDe(hid)}
              style={{
                display: "block",
                fontSize: 12,
                fontWeight: 500,
                padding: "3px 8px",
                borderRadius: 6,
                color: "#fff",
                background: corDe(hid),
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {nomeDe(hid)}
            </span>
          ))
        )}
      </div>
    </div>
  );
}

function Legenda({ dados, corDe }) {
  const nomes = Object.fromEntries(dados.camadas.map((c) => [c.chave, c.nome]));
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="section-title" style={{ marginBottom: 8 }}>{t("Servidores e o que cada um carrega")}</div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>{t("Servidor")}</th>
              <th>{t("Papel")}</th>
              <th>{t("GPU")}</th>
              <th>{t("Camadas que executa")}</th>
            </tr>
          </thead>
          <tbody>
            {dados.servidores.map((s) => (
              <tr key={s.host_id}>
                <td>
                  <span className="stack-h" style={{ gap: 8, alignItems: "center" }}>
                    <span
                      style={{
                        width: 12,
                        height: 12,
                        borderRadius: 3,
                        background: corDe(s.host_id),
                        flex: "0 0 auto",
                      }}
                    />
                    <strong>{s.host}</strong>
                  </span>
                  <div className="small muted mono" style={{ marginLeft: 20 }}>{s.endereco}</div>
                </td>
                <td className="small">{s.papel || "—"}</td>
                <td className="small">{s.gpus > 0 ? `${s.gpus} GPU(s)` : "—"}</td>
                <td>
                  {s.erro ? (
                    <span className="small" style={{ color: "var(--red)" }}>
                      sem comunicação: {s.erro}
                    </span>
                  ) : s.camadas.length === 0 ? (
                    <span className="small muted">{t("nenhuma do Face Detect")}</span>
                  ) : (
                    <span className="stack-h" style={{ flexWrap: "wrap", gap: 5 }}>
                      {s.camadas.map((c) => (
                        <span
                          key={c}
                          className="mono"
                          style={{
                            fontSize: 11,
                            padding: "2px 6px",
                            borderRadius: 5,
                            background: "var(--bg-2)",
                            border: "1px solid var(--border)",
                          }}
                        >
                          {nomes[c] || c}
                        </span>
                      ))}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="small muted" style={{ marginTop: 8 }}>
        O fluxo acima é a cadeia de dependências do Face Detect: a câmera alimenta
        o Vídeo, que alimenta a Extração, que gera vetores comparados na Busca
        contra o Tarantool (Vetores), com os dados no PostgreSQL. A Aplicação
        consome Busca, Dados e Mídia para montar a tela.
      </div>
    </div>
  );
}
