import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import {
  Carregando, Erro, SeletorHost, SeletorPeriodo, useHosts,
} from "../Comuns";
import { GraficoMultiLinha, corDaSerie } from "../Graficos";
import { IconAlerta, IconAtualizar, IconFechar, IconOk, IconTendencia } from "../Icons";

/**
 * Crescimento — o que está subindo, quando estoura e quem está empurrando.
 *
 * O Monitor mostra o valor de agora; o Rastreio mostra o que já quebrou.
 * Esta tela responde a pergunta do meio, que é a única que ainda dá tempo
 * de agir: **isto está subindo sem parar, e vai me derrubar quando?**
 *
 * Quatro blocos, na ordem em que a pergunta se desenrola:
 *
 * 1. **os três recursos** (memória, disco, swap) com a taxa medida, a
 *    projeção até o limite e o que quebra quando chegar lá;
 * 2. **todos os containers num gráfico só**, para comparar quem sobe
 *    contra quem fica parado;
 * 3. **um container por vez** — a tabela com os números exatos e, ao
 *    abrir, o gráfico dele sozinho (memória e CPU). Comparar precisa de
 *    todo mundo junto; investigar precisa de um só, sem as outras linhas
 *    no caminho;
 * 4. **as vigilâncias** abertas, com o culpado, por que ele cresce, o que
 *    o fabricante recomenda e o contorno.
 *
 * Custo: tudo isso lê o banco — a série que o coletor já gravou a partir
 * do `docker stats` que ele faz de qualquer forma. Abrir a tela e trocar
 * o período não tocam em servidor nenhum. Só "Rastrear agora" abre SSH, e
 * por isso é botão, não intervalo.
 */

const CORES = {
  critico: { fundo: "var(--red-bg)", borda: "var(--red-bd)", texto: "var(--red-fg)" },
  atencao: { fundo: "var(--amber-bg)", borda: "var(--amber-bd)", texto: "var(--amber-fg)" },
  info: { fundo: "var(--bg-2)", borda: "var(--border)", texto: "var(--text-2)" },
};

const REGIME = {
  acelerando: "acelerando",
  linear: "subindo",
  serrote: "sobe e volta",
  estavel: "estável",
  recuando: "recuando",
  indeterminado: "sem tendência",
};

function mb(valor) {
  if (valor === null || valor === undefined) return "—";
  const v = Number(valor);
  return v >= 1024
    ? `${(v / 1024).toFixed(2).replace(".", ",")} GB`
    : `${v.toFixed(0)} MB`;
}

function sinal(valor, unidade = " MB/h") {
  if (valor === null || valor === undefined) return "—";
  const v = Number(valor);
  const texto = `${Math.abs(v).toFixed(1).replace(".", ",")}${unidade}`;
  if (v > 0) return `+${texto}`;
  if (v < 0) return `−${texto}`;
  return `0${unidade}`;
}

function curto(nome) {
  // O prefixo do projeto se repete em todos e rouba a coluna inteira. O
  // nome completo continua no title e no gráfico individual.
  return nome.replace(/^findface-multi-/, "");
}

function periodo(de, ate) {
  if (!de || !ate) return "";
  const d = new Date(de);
  const a = new Date(ate);
  const opcoes = { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" };
  return `${d.toLocaleString("pt-BR", opcoes)} → ${a.toLocaleString("pt-BR", opcoes)}`;
}

/** Um recurso: onde está, para onde vai, e o que quebra se chegar lá. */
function CartaoRecurso({ dado }) {
  const subindo = dado.regime === "linear" || dado.regime === "acelerando";
  const cor = dado.preocupa ? CORES[dado.nivel] || CORES.atencao : CORES.info;

  return (
    <div className="card" style={{ borderColor: cor.borda, background: cor.fundo }}>
      <div className="stat-top">
        <span className="stat-label" style={{ textTransform: "capitalize" }}>
          {dado.rotulo}
        </span>
        <span className="pill pill-idle">{REGIME[dado.regime] || dado.regime}</span>
      </div>
      <div className="stat-value" style={{ color: subindo ? cor.texto : undefined }}>
        {(dado.valor_atual ?? 0).toFixed(1).replace(".", ",")}%
      </div>
      {dado.absoluto && <div className="small muted">{dado.absoluto}</div>}

      {subindo ? (
        <div className="small" style={{ marginTop: 6 }}>
          {dado.regime === "acelerando" && dado.dobra_h
            ? `Dobra a cada ${
                dado.dobra_h < 1
                  ? `${Math.round(dado.dobra_h * 60)} min`
                  : `${dado.dobra_h.toFixed(1).replace(".", ",")} h`
              }`
            : `${sinal(dado.taxa_por_h, " ponto(s)/h")}`}
          {dado.horas_ate_teto_texto && (
            <>
              {" · "}chega a {dado.teto_pct}% em{" "}
              <strong>{dado.horas_ate_teto_texto}</strong>
            </>
          )}
        </div>
      ) : (
        <div className="small muted" style={{ marginTop: 6 }}>
          {dado.motivo || "Não está subindo na janela analisada."}
        </div>
      )}

      {dado.dano && (
        <div className="small" style={{ marginTop: 6 }}>
          <strong>Se chegar lá:</strong> {dado.dano}
        </div>
      )}

      <div className="small muted" style={{ marginTop: 6 }}>
        {dado.pontos} ponto(s) · R² {dado.r2} · confiança {dado.confianca}
      </div>
    </div>
  );
}

/**
 * Um container sozinho: memória e CPU no mesmo período, com os números
 * exatos ao lado.
 *
 * Dois gráficos separados, e não dois eixos no mesmo: MB e % não
 * compartilham escala, e sobrepô-los faz duas curvas se cruzarem em
 * pontos que não significam nada.
 */
function DetalheContainer({ serie, cor, onFechar }) {
  const memoria = [{ nome: serie.nome, cor, pontos: serie.pontos.map((p) => ({ ts: p.ts, valor: p.mem_mb })) }];
  const cpu = [{ nome: `${serie.nome} cpu`, cor: "var(--amber-fg)", pontos: serie.pontos.map((p) => ({ ts: p.ts, valor: p.cpu_pct })) }];

  const numeros = [
    ["Agora", mb(serie.atual_mb)],
    ["Mínimo", mb(serie.minimo_mb)],
    ["Média", mb(serie.media_mb)],
    ["Pico", mb(serie.pico_mb)],
    ["Variação na janela", sinal(serie.variacao_mb, " MB")],
    ["Ritmo", sinal(serie.mb_por_h)],
    ["CPU agora / média / pico",
     `${serie.cpu_pct}% / ${serie.cpu_media}% / ${serie.cpu_pico}%`],
    ["Qualidade do ajuste", `R² ${serie.r2}`],
    ["Amostras", `${serie.amostras}`],
  ];

  return (
    <div className="card" style={{ borderColor: cor }}>
      <div className="stat-top" style={{ marginBottom: 6 }}>
        <div>
          <div style={{ fontWeight: 600 }} className="mono">
            {serie.nome}
          </div>
          <div className="small muted">{periodo(serie.de, serie.ate)}</div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={onFechar}>
          <IconFechar size={14} /> Fechar
        </button>
      </div>

      <div className="small muted" style={{ marginBottom: 2 }}>Memória</div>
      <GraficoMultiLinha series={memoria} altura={180} />

      <div className="small muted" style={{ margin: "10px 0 2px" }}>CPU</div>
      <GraficoMultiLinha
        series={cpu}
        altura={120}
        unidade="%"
        formatar={(v) => `${v.toFixed(1).replace(".", ",")}%`}
      />

      <div className="grid-stats" style={{ marginTop: 12 }}>
        {numeros.map(([rotulo, valor]) => (
          <div className="card card-tight stat" key={rotulo}>
            <span className="stat-label">{rotulo}</span>
            <div className="stat-value" style={{ fontSize: 16 }}>{valor}</div>
          </div>
        ))}
      </div>

      {serie.amostras < 3 && (
        <div className="small muted" style={{ marginTop: 8 }}>
          Menos de três amostras: o ritmo mostrado é o que dá para calcular,
          não uma tendência.
        </div>
      )}
    </div>
  );
}

/** Uma vigilância aberta: culpado, causa, contorno e o que o manual diz. */
function CartaoVigilancia({ vig, onRastrear, rastreando }) {
  const cor = CORES[vig.nivel] || CORES.atencao;
  const culpados = (vig.diagnostico || {}).culpados_serie || [];
  const achados = (vig.diagnostico || {}).achados || [];
  const crescendo = (vig.diagnostico || {}).crescendo || [];

  return (
    <div className="card" style={{ borderColor: cor.borda, background: cor.fundo }}>
      <div className="stack-h" style={{ alignItems: "flex-start", gap: 10 }}>
        <IconAlerta size={18} />
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, color: cor.texto }}>
            {vig.rotulo} de {vig.valor_inicial}% para {vig.valor_atual}%
            <span className="pill pill-idle" style={{ marginLeft: 8 }}>
              {REGIME[vig.regime] || vig.regime}
            </span>
          </div>

          <div className="mono small" style={{ marginTop: 6 }}>
            {sinal(vig.taxa_por_h, " ponto(s)/h")}
            {vig.dobra_h ? ` · dobra a cada ${vig.dobra_h} h` : ""}
            {vig.estouro_em
              ? ` · ${vig.teto_pct}% previsto para ${new Date(
                  vig.estouro_em,
                ).toLocaleString("pt-BR")}`
              : ""}
          </div>

          <div className="small" style={{ marginTop: 6 }}>
            <strong>Desde:</strong> {new Date(vig.inicio).toLocaleString("pt-BR")} ·{" "}
            {vig.ciclos} ciclo(s) confirmando
          </div>

          {vig.culpado ? (
            <div className="small" style={{ marginTop: 4 }}>
              <strong>Quem está empurrando:</strong>{" "}
              <span className="mono">{vig.culpado}</span>
            </div>
          ) : (
            <div className="small muted" style={{ marginTop: 4 }}>
              Ainda sem culpado identificado. Rastrear consulta o servidor.
            </div>
          )}

          {culpados.length > 0 && (
            <ul className="small" style={{ marginTop: 6, paddingLeft: 18 }}>
              {culpados.map((c) => (
                <li key={c.nome}>
                  <span className="mono">{c.nome}</span> — {sinal(c.mb_por_h)}
                  {c.por_que ? ` · ${c.por_que}` : ""}
                </li>
              ))}
            </ul>
          )}

          {crescendo.length > 0 && (
            <ul className="small" style={{ marginTop: 6, paddingLeft: 18 }}>
              {crescendo.slice(0, 5).map((c) => (
                <li key={c.alvo}>
                  <span className="mono">{c.alvo}</span> — cresceu{" "}
                  {mb(c.cresceu_bytes / (1024 * 1024))} em {c.horas} h
                </li>
              ))}
            </ul>
          )}

          {achados.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary className="small">
                Evidência do rastreio ({achados.length})
              </summary>
              <ul className="small mono" style={{ paddingLeft: 18, marginTop: 4 }}>
                {achados.map((a, i) => (
                  <li key={i}>
                    [{a.fonte}] {a.texto}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <div className="form-acao" style={{ marginTop: 8 }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => onRastrear(vig.id)}
              disabled={rastreando}
            >
              <IconAtualizar size={14} />{" "}
              {rastreando ? "Rastreando…" : "Rastrear agora"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function CrescimentoView() {
  const { hosts, hostId, setHostId, carregando: carregandoHosts } = useHosts(true);
  const [analise, setAnalise] = useState(null);
  const [containers, setContainers] = useState(null);
  // Janela relativa por padrão; vira {de, ate} quando alguém fixa um
  // intervalo. Ver SeletorPeriodo.
  const [periodo, setPeriodo] = useState({ horas: 6 });
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [rastreando, setRastreando] = useState(0);
  const [ocultos, setOcultos] = useState(() => new Set());
  const [destaque, setDestaque] = useState("");
  const [aberto, setAberto] = useState("");
  const [filtro, setFiltro] = useState("");
  const [ordem, setOrdem] = useState({ campo: "mb_por_h", desc: true });

  const carregar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    try {
      const [a, c] = await Promise.all([
        api.crescimentoAnalise(hostId, periodo),
        api.crescimentoContainers(hostId, periodo),
      ]);
      setAnalise(a);
      setContainers(c);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, [hostId, periodo]);

  // Lê do banco, não do servidor: abrir a tela e trocar o período são
  // baratos, então carregam sozinhos. O que custa SSH aqui é só o
  // rastreio, que continua no clique.
  useEffect(() => {
    carregar();
  }, [carregar]);

  // Trocar de servidor zera a seleção: container aberto de outra máquina
  // continuaria na tela mostrando dado que não é de lá.
  useEffect(() => {
    setAberto("");
    setOcultos(new Set());
  }, [hostId]);

  const rastrear = async (id) => {
    setRastreando(id);
    try {
      await api.crescimentoRastrear(id);
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setRastreando(0);
    }
  };

  const series = useMemo(() => {
    const lista = (containers && containers.series) || [];
    return lista.map((s, i) => ({ ...s, cor: corDaSerie(i) }));
  }, [containers]);

  const filtradas = useMemo(() => {
    const alvo = filtro.trim().toLowerCase();
    const lista = alvo
      ? series.filter((s) => s.nome.toLowerCase().includes(alvo))
      : series;
    const sinal = ordem.desc ? -1 : 1;
    return [...lista].sort((a, b) => {
      if (ordem.campo === "nome") return sinal * a.nome.localeCompare(b.nome);
      return sinal * ((Number(a[ordem.campo]) || 0) - (Number(b[ordem.campo]) || 0));
    });
  }, [series, filtro, ordem]);

  // Clicar na mesma coluna inverte; clicar noutra começa do maior, que é
  // o que quase sempre se procura numa tabela de consumo.
  const ordenarPor = (campo) =>
    setOrdem((antes) =>
      antes.campo === campo ? { campo, desc: !antes.desc } : { campo, desc: true },
    );

  const seta = (campo) =>
    ordem.campo === campo ? (ordem.desc ? " ↓" : " ↑") : "";

  const paraGrafico = useMemo(
    () =>
      filtradas.map((s) => ({
        nome: s.nome,
        cor: s.cor,
        pontos: s.pontos.map((p) => ({ ts: p.ts, valor: p.mem_mb })),
      })),
    [filtradas],
  );

  const visiveis = useMemo(
    () => new Set(filtradas.filter((s) => !ocultos.has(s.nome)).map((s) => s.nome)),
    [filtradas, ocultos],
  );

  const alternar = (nome) =>
    setOcultos((antes) => {
      const novo = new Set(antes);
      if (novo.has(nome)) novo.delete(nome);
      else novo.add(nome);
      return novo;
    });

  const serieAberta = series.find((s) => s.nome === aberto) || null;

  if (carregandoHosts) return <Carregando />;

  const COLUNAS = [
    ["mb_por_h", "Ritmo"],
    ["atual_mb", "Agora"],
    ["pico_mb", "Pico"],
    ["media_mb", "Média"],
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">Crescimento</div>
          <div className="page-sub">
            O que está subindo, quando encosta no limite e qual container
            está consumindo
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <button className="btn btn-ghost" onClick={carregar} disabled={carregando}>
            <IconAtualizar size={15} /> Recarregar
          </button>
        </div>
      </div>

      <div className="card card-tight" style={{ marginBottom: 14 }}>
        <SeletorPeriodo
          valor={periodo}
          onMudar={setPeriodo}
          disponivelDesde={
            (containers && containers.mais_antiga) ||
            (analise && analise.mais_antiga)
          }
          retencaoDias={containers && containers.retencao_dias}
          rotuloDado="série por container"
        />
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {carregando && !analise && <Carregando texto="Lendo o histórico do painel…" />}

      {analise && (
        <div className="stack-v">
          <div className="grid-stats">
            {analise.recursos.map((r) => (
              <CartaoRecurso key={r.recurso} dado={r} />
            ))}
          </div>

          {analise.amostras < 8 && (
            <div className="card card-tight">
              <span className="small muted">
                <IconAlerta size={14} /> {analise.amostras} amostra(s) na janela.
                Abaixo de oito o painel não afirma tendência — o coletor precisa
                de mais tempo neste servidor.
              </span>
            </div>
          )}

          {/* ── Todos os containers, para comparar ───────────────────── */}
          <div className="card">
            <div className="stat-top" style={{ marginBottom: 8 }}>
              <div>
                <div style={{ fontWeight: 600 }}>
                  <IconTendencia size={16} /> Memória por container
                </div>
                <div className="small muted">
                  Clique na legenda para esconder uma linha; passe o mouse para
                  destacar. Para ver um sozinho, abra na tabela abaixo.
                </div>
              </div>
              <input
                type="search"
                placeholder="filtrar container…"
                value={filtro}
                onChange={(e) => setFiltro(e.target.value)}
                aria-label="Filtrar containers pelo nome"
                style={{ maxWidth: 240 }}
              />
            </div>

            {!series.length ? (
              <div className="small muted">
                {(containers && containers.motivo) ||
                  "Ainda não há histórico por container nesta janela."}
              </div>
            ) : (
              <>
                <GraficoMultiLinha
                  series={paraGrafico}
                  visiveis={visiveis}
                  destaque={destaque || aberto}
                  altura={300}
                />

                <div
                  className="stack-h"
                  style={{ flexWrap: "wrap", gap: 6, marginTop: 10 }}
                >
                  {filtradas.map((s) => (
                    <button
                      key={s.nome}
                      className="btn btn-ghost btn-sm"
                      onClick={() => alternar(s.nome)}
                      onMouseEnter={() => setDestaque(s.nome)}
                      onMouseLeave={() => setDestaque("")}
                      title={`${s.nome} — agora ${mb(s.atual_mb)}, pico ${mb(
                        s.pico_mb,
                      )}, ${sinal(s.mb_por_h)}`}
                      style={{ opacity: ocultos.has(s.nome) ? 0.4 : 1 }}
                    >
                      <span
                        style={{
                          display: "inline-block",
                          width: 10,
                          height: 10,
                          borderRadius: 2,
                          background: s.cor,
                          marginRight: 6,
                        }}
                      />
                      <span className="mono">{curto(s.nome)}</span>
                      <span className="small muted" style={{ marginLeft: 6 }}>
                        {mb(s.atual_mb)}
                      </span>
                    </button>
                  ))}
                </div>

                {containers.fora_do_teto > 0 && (
                  <div className="small muted" style={{ marginTop: 6 }}>
                    Mais {containers.fora_do_teto} container(es) fora do teto da
                    tela, todos com crescimento menor que os mostrados.
                  </div>
                )}
              </>
            )}
          </div>

          {/* ── Um por vez ───────────────────────────────────────────── */}
          {serieAberta && (
            <DetalheContainer
              serie={serieAberta}
              cor={serieAberta.cor}
              onFechar={() => setAberto("")}
            />
          )}

          {series.length > 0 && (
            <div className="card">
              <div className="stat-top" style={{ marginBottom: 8 }}>
                <div style={{ fontWeight: 600 }}>Containers, um a um</div>
                <div className="stack-h" style={{ gap: 4, flexWrap: "wrap" }}>
                  <span className="small muted">ordenar por</span>
                  {COLUNAS.map(([chave, rotulo]) => (
                    <button
                      key={chave}
                      className={`btn btn-sm ${
                        ordem.campo === chave ? "btn-primary" : "btn-ghost"
                      }`}
                      onClick={() => ordenarPor(chave)}
                      title={`Ordenar por ${rotulo.toLowerCase()} — clique de novo para inverter`}
                    >
                      {rotulo}
                      {seta(chave)}
                    </button>
                  ))}
                </div>
              </div>

              <div className="table-wrap">
                <table className="tabela-densa">
                  <thead>
                    <tr>
                      {[
                        ["nome", "Container", "left"],
                        ["atual_mb", "Agora", "right"],
                        ["minimo_mb", "Mínimo", "right"],
                        ["media_mb", "Média", "right"],
                        ["pico_mb", "Pico", "right"],
                        ["variacao_mb", "Na janela", "right"],
                        ["mb_por_h", "Ritmo", "right"],
                        ["cpu_pct", "CPU", "right"],
                        ["amostras", "Amostras", "right"],
                      ].map(([campo, rotulo, lado]) => (
                        <th
                          key={campo}
                          onClick={() => ordenarPor(campo)}
                          style={{ textAlign: lado, cursor: "pointer", userSelect: "none" }}
                          title="Clique para ordenar; de novo para inverter"
                        >
                          {rotulo}
                          {seta(campo)}
                        </th>
                      ))}
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {filtradas.map((s) => (
                      <tr
                        key={s.nome}
                        style={{
                          background:
                            aberto === s.nome ? "var(--bg-2)" : undefined,
                        }}
                      >
                        <td className="mono" title={s.nome}>
                          <span
                            style={{
                              display: "inline-block",
                              width: 10,
                              height: 10,
                              borderRadius: 2,
                              background: s.cor,
                              marginRight: 6,
                            }}
                          />
                          {curto(s.nome)}
                        </td>
                        <td className="mono" style={{ textAlign: "right" }}>
                          {mb(s.atual_mb)}
                        </td>
                        <td className="mono muted" style={{ textAlign: "right" }}>
                          {mb(s.minimo_mb)}
                        </td>
                        <td className="mono muted" style={{ textAlign: "right" }}>
                          {mb(s.media_mb)}
                        </td>
                        <td className="mono" style={{ textAlign: "right" }}>
                          {mb(s.pico_mb)}
                        </td>
                        <td
                          className="mono"
                          style={{
                            textAlign: "right",
                            color: s.variacao_mb > 0 ? "var(--red-fg)" : undefined,
                          }}
                        >
                          {sinal(s.variacao_mb, " MB")}
                        </td>
                        <td
                          className="mono"
                          style={{
                            textAlign: "right",
                            color: s.mb_por_h > 0 ? "var(--red-fg)" : undefined,
                          }}
                        >
                          {sinal(s.mb_por_h)}
                        </td>
                        <td className="mono muted" style={{ textAlign: "right" }}>
                          {s.cpu_pct}%
                        </td>
                        <td className="mono muted" style={{ textAlign: "right" }}>
                          {s.amostras}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <button
                            className="btn btn-ghost btn-sm"
                            onClick={() =>
                              setAberto(aberto === s.nome ? "" : s.nome)
                            }
                          >
                            {aberto === s.nome ? "fechar" : "ver gráfico"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="small muted" style={{ marginTop: 6 }}>
                Ritmo é a inclinação da reta ajustada à série, em MB por hora.
                "Na janela" é a diferença entre a primeira e a última leitura do
                período escolhido.
              </div>
            </div>
          )}

          {/* ── Culpados de memória, direto da série ─────────────────── */}
          {analise.culpados_memoria && analise.culpados_memoria.length > 0 && (
            <div className="card">
              <div style={{ fontWeight: 600, marginBottom: 6 }}>
                Containers que cresceram na janela
              </div>
              {analise.culpados_memoria.map((c) => (
                <div key={c.nome} style={{ marginBottom: 10 }}>
                  <div className="mono small">
                    {c.nome} — {sinal(c.mb_por_h)}, hoje em {mb(c.atual_mb)}
                  </div>
                  {c.por_que && (
                    <div className="small" style={{ marginTop: 2 }}>
                      <strong>Por quê:</strong> {c.por_que}
                    </div>
                  )}
                  {c.contorno && (
                    <div className="small" style={{ marginTop: 2 }}>
                      <strong>O que fazer:</strong> {c.contorno}
                    </div>
                  )}
                  {c.fabricante && (
                    <div className="small muted" style={{ marginTop: 2 }}>
                      Fabricante: {c.fabricante}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* ── Vigilâncias ──────────────────────────────────────────── */}
          {analise.vigilancias.length > 0 ? (
            analise.vigilancias.map((v) => (
              <CartaoVigilancia
                key={v.id}
                vig={v}
                onRastrear={rastrear}
                rastreando={rastreando === v.id}
              />
            ))
          ) : (
            <div
              className="card card-tight"
              style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)" }}
            >
              <span className="small" style={{ color: "var(--green-fg)" }}>
                <IconOk size={14} /> Nenhum recurso em subida contínua neste
                servidor agora. Isso vale para a janela analisada — não é
                promessa sobre o resto do dia.
              </span>
            </div>
          )}

          <div className="card card-tight">
            <div className="small muted">
              {analise.lembretes.map((l, i) => (
                <div key={i}>· {l}</div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
