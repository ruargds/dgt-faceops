import React, { useCallback, useEffect, useState } from "react";
import { api } from "../../api";
import { t } from "../../i18n";
import { Carregando, Erro, SeletorHost, useHosts } from "../Comuns";
import { IconAlerta, IconAtualizar, IconOk } from "../Icons";

/**
 * Rastreio — o que está comprometendo o funcionamento agora.
 *
 * O painel já mostrava sintomas espalhados: disco numa tela, licença
 * noutra, container numa terceira. Esta tela responde a pergunta que quem
 * está de plantão faz de verdade — "tem algo quebrado?" — juntando licença,
 * componentes internos, disco, coleta, backup e segurança.
 *
 * Cada achado carrega quatro coisas, e nenhuma delas é enfeite:
 *
 * * **evidência** — o número ou a mensagem que o servidor devolveu, para
 *   ninguém precisar acreditar no painel;
 * * **impacto** — o que para de funcionar, em termos de operação;
 * * **ação** — onde clicar. Achado sem ação vira ansiedade;
 * * **origem** — para separar "isso é licença" de "isso é disco".
 *
 * Sob demanda, sempre: são duas execuções SSH por servidor. E só leitura —
 * nada aqui reinicia, apaga ou conserta sozinho. Diagnóstico que age por
 * conta própria é alarme de incêndio que abre a janela.
 */
const CORES = {
  critico: { fundo: "var(--red-bg)", borda: "var(--red-bd)", texto: "var(--red-fg)" },
  atencao: { fundo: "var(--amber-bg)", borda: "var(--amber-bd)", texto: "var(--amber-fg)" },
  info: { fundo: "var(--bg-2)", borda: "var(--border)", texto: "var(--text-2)" },
};

const ROTULO_NIVEL = { critico: "Crítico", atencao: "Atenção", info: "Informação" };

export default function RastreioView() {
  const { hosts, hostId, setHostId, carregando: carregandoHosts } = useHosts(false);
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [rodando, setRodando] = useState(false);
  const [filtro, setFiltro] = useState("");

  const rastrear = useCallback(async () => {
    setRodando(true);
    setErro("");
    try {
      setDados(await api.rastreio(hostId));
    } catch (ex) {
      setDados(null);
      setErro(ex.message);
    } finally {
      setRodando(false);
    }
  }, [hostId]);

  // Não roda sozinho ao abrir: são execuções SSH em servidor de produção, e
  // a tela precisa ser aberta sem custo. O clique é o consentimento.
  useEffect(() => {
    setDados(null);
  }, [hostId]);

  if (carregandoHosts) return <Carregando />;

  const achados = dados
    ? dados.achados.filter((a) => !filtro || a.nivel === filtro)
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">Rastreio</div>
          <div className="page-sub">
            O que está comprometendo o funcionamento — com a evidência, o
            impacto e o que fazer
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} incluirTodos />
          <button className="btn btn-primary" onClick={rastrear} disabled={rodando}>
            <IconAtualizar size={15} /> {rodando ? "Rastreando…" : "Rastrear"}
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={rastrear} />

      {!dados && !rodando && !erro && (
        <div className="card">
          <div className="small muted">
            Clique em <strong>Rastrear</strong>. São duas leituras por servidor
            (componentes e licença), mais as checagens locais do painel — nada
            roda sozinho aqui, porque cada leitura é um SSH em produção.
            <br />
            <br />
            O rastreio confere: validade e limites da licença, componente
            travado (container de pé e serviço sem responder), container
            reiniciando em laço, disco perto de encher, coletor parado, backup
            do painel ausente, destino de backup faltando, última execução com
            falha e senha de fábrica em uso.
          </div>
        </div>
      )}

      {rodando && !dados && (
        <Carregando texto="Perguntando aos servidores e conferindo o painel…" />
      )}

      {dados && (
        <div className="stack-v">
          <div className="grid-stats">
            {[
              ["critico", dados.criticos, "Param ou já pararam a operação"],
              ["atencao", dados.atencao, "Vão parar se ninguém agir"],
              ["info", dados.info, "Vale saber, não urge"],
            ].map(([nivel, quantos, sub]) => (
              <div
                className="card card-tight stat"
                key={nivel}
                title={sub}
                style={{
                  cursor: "pointer",
                  borderColor: filtro === nivel ? CORES[nivel].texto : undefined,
                }}
                onClick={() => setFiltro(filtro === nivel ? "" : nivel)}
              >
                <span className="stat-label">{ROTULO_NIVEL[nivel]}</span>
                <div className="stat-value" style={{ color: CORES[nivel].texto }}>
                  {quantos}
                </div>
              </div>
            ))}
            <div className="card card-tight stat" title="Servidores consultados neste rastreio">
              <span className="stat-label">Servidores</span>
              <div className="stat-value">{dados.servidores.length}</div>
            </div>
          </div>

          {dados.achados.length === 0 && (
            <div
              className="card card-tight"
              style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)" }}
            >
              <span className="small" style={{ color: "var(--green-fg)" }}>
                <IconOk size={14} /> Nada comprometendo o funcionamento nas
                checagens que o painel sabe fazer. Isso não é o mesmo que
                "nenhum problema existe" — é "nenhum destes".
              </span>
            </div>
          )}

          {filtro && (
            <div className="small muted">
              Mostrando só {ROTULO_NIVEL[filtro].toLowerCase()}.{" "}
              <button className="btn btn-ghost btn-sm" onClick={() => setFiltro("")}>
                ver tudo
              </button>
            </div>
          )}

          {achados.map((a, i) => {
            const cor = CORES[a.nivel] || CORES.info;
            return (
              <div
                className="card"
                key={`${a.titulo}-${a.servidor}-${i}`}
                style={{ borderColor: cor.borda, background: cor.fundo }}
              >
                <div className="stack-h" style={{ alignItems: "flex-start", gap: 10 }}>
                  {a.nivel === "info" ? (
                    <IconOk size={18} />
                  ) : (
                    <IconAlerta size={18} />
                  )}
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, color: cor.texto }}>
                      {a.titulo}
                      {a.servidor && (
                        <span className="mono small" style={{ marginLeft: 8, fontWeight: 400 }}>
                          {a.servidor}
                        </span>
                      )}
                      <span className="pill pill-idle" style={{ marginLeft: 8 }}>
                        {a.origem}
                      </span>
                    </div>

                    <div className="mono small" style={{ marginTop: 6 }}>
                      {a.evidencia}
                    </div>

                    <div className="small" style={{ marginTop: 6 }}>
                      <strong>Impacto:</strong> {a.impacto}
                    </div>
                    <div className="small" style={{ marginTop: 2 }}>
                      <strong>O que fazer:</strong> {a.acao}
                    </div>
                    {a.manual && (
                      <div className="small muted" style={{ marginTop: 4 }}>
                        Critério do fabricante: {a.manual}.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          <div className="small muted">
            Rastreio de {new Date(dados.em).toLocaleString("pt-BR")}. Só leitura:
            nenhuma checagem reiniciou serviço, apagou dado ou mudou
            configuração. {t("Recarregar")} para ver o estado de agora.
          </div>
        </div>
      )}
    </>
  );
}
