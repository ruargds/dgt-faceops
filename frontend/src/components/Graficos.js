import React from "react";

/**
 * Gráficos em SVG puro, sem biblioteca.
 *
 * Um gráfico de linha é uma lista de pontos e um `<path>`. Trazer
 * Chart.js ou Recharts para isso somaria centenas de KB ao pacote e uma
 * dependência para manter — num painel cujo compromisso é ser leve.
 *
 * O eixo Y é sempre 0–100%: são todas medidas percentuais, e escala
 * automática esconderia justamente o que interessa (estar perto do teto).
 */

const COR = {
  ok: "var(--green)",
  atencao: "var(--amber)",
  critico: "var(--red)",
  neutro: "var(--blue)",
};

function corPara(valor, limite) {
  if (valor >= limite) return COR.critico;
  if (valor >= limite * 0.85) return COR.atencao;
  return COR.ok;
}

/**
 * Linha do tempo de uma métrica.
 *
 * `serie` = [{ts, valor}]. `limite` desenha a linha tracejada do alerta —
 * ver o teto junto com a curva é o que transforma número em decisão.
 */
export function GraficoLinha({
  serie,
  limite = null,
  altura = 90,
  cor = null,
  rotulo = "",
  unidade = "%",
  maximo = 100,
}) {
  const L = 8, R = 8, T = 8, B = 18;
  const W = 600;
  const H = altura;
  const areaW = W - L - R;
  const areaH = H - T - B;

  if (!serie || serie.length === 0) {
    return (
      <div
        className="small muted"
        style={{
          height: altura,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
        }}
      >
        Sem histórico ainda — o coletor grava a primeira amostra em até 1 minuto.
      </div>
    );
  }

  const n = serie.length;
  const px = (i) => L + (n === 1 ? areaW / 2 : (i / (n - 1)) * areaW);
  const py = (v) => T + areaH - (Math.min(Math.max(v, 0), maximo) / maximo) * areaH;

  const pontos = serie.map((p, i) => `${px(i).toFixed(1)},${py(p.valor).toFixed(1)}`);
  const linha = `M ${pontos.join(" L ")}`;
  const area = `${linha} L ${px(n - 1).toFixed(1)},${(T + areaH).toFixed(1)} L ${px(0).toFixed(1)},${(T + areaH).toFixed(1)} Z`;

  const ultimo = serie[n - 1].valor;
  const pico = Math.max(...serie.map((p) => p.valor));
  const traco = cor || (limite !== null ? corPara(ultimo, limite) : COR.neutro);

  const idGrad = `g${rotulo.replace(/\W/g, "")}${Math.round(altura)}`;

  // O SVG estica horizontalmente (preserveAspectRatio="none") para
  // preencher a largura variável. Isso distorceria texto e círculos —
  // então DENTRO do SVG vão só linhas e áreas (que esticam bem), e os
  // rótulos ficam como HTML por cima, que não distorce. Como a altura do
  // viewBox é igual à altura real, py() já dá o pixel vertical certo.
  //
  // Em gráfico pequeno (mini/faísca) os rótulos poluem — só aparecem no
  // gráfico grande.
  const mini = altura < 60;
  const topLimite = limite !== null && limite <= maximo ? py(limite) : null;

  // ── Eixo de tempo ──────────────────────────────────────────────────
  // Sem ele o gráfico mostra a forma e esconde o quando: "esse pico foi às
  // 3h ou anteontem?" não tinha resposta na tela. O formato acompanha o
  // intervalo real da série, não a janela pedida — se o coletor só tem
  // duas horas de histórico, mostrar dia/mês seria falso.
  const carimbos = serie.map((p) => (p.ts ? new Date(p.ts) : null));
  const primeiro = carimbos[0];
  const ultimoTs = carimbos[n - 1];
  const spanMs =
    primeiro && ultimoTs && !isNaN(primeiro) && !isNaN(ultimoTs)
      ? ultimoTs - primeiro
      : 0;

  const formatar = (d) => {
    if (!d || isNaN(d)) return "";
    const hm = { hour: "2-digit", minute: "2-digit" };
    if (spanMs <= 2 * 3600e3) {
      return d.toLocaleTimeString("pt-BR", { ...hm, second: "2-digit" });
    }
    if (spanMs <= 36 * 3600e3) {
      return d.toLocaleTimeString("pt-BR", hm);
    }
    if (spanMs <= 8 * 24 * 3600e3) {
      return (
        d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) +
        " " +
        d.toLocaleTimeString("pt-BR", hm)
      );
    }
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  };

  // Quatro marcas bastam: mais que isso empilha texto em tela estreita, e
  // a leitura que importa é "quando começa, quando termina, e onde caiu".
  const marcas = [];
  if (!mini && spanMs > 0) {
    const quantas = Math.min(4, n);
    for (let k = 0; k < quantas; k++) {
      const i = Math.round((k / (quantas - 1)) * (n - 1));
      marcas.push({
        i,
        pctX: n === 1 ? 50 : (i / (n - 1)) * 100,
        texto: formatar(carimbos[i]),
      });
    }
  }

  return (
    <div
      style={{
        position: "relative",
        width: "100%",
        height: altura + (marcas.length ? 16 : 0),
      }}
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        style={{ width: "100%", height: altura, display: "block" }}
        role="img"
        aria-label={`${rotulo}: ${ultimo}${unidade}, pico de ${pico}${unidade}`}
      >
        <defs>
          <linearGradient id={idGrad} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={traco} stopOpacity="0.22" />
            <stop offset="100%" stopColor={traco} stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {[25, 50, 75].map((v) => (
          <line
            key={v}
            x1={L} x2={W - R}
            y1={py((v / 100) * maximo)} y2={py((v / 100) * maximo)}
            stroke="var(--border)" strokeWidth="1" strokeDasharray="2 4"
            vectorEffect="non-scaling-stroke"
          />
        ))}

        {topLimite !== null && (
          <line
            x1={L} x2={W - R} y1={topLimite} y2={topLimite}
            stroke="var(--red)" strokeWidth="1.5" strokeDasharray="5 3"
            opacity="0.5" vectorEffect="non-scaling-stroke"
          />
        )}

        <path d={area} fill={`url(#${idGrad})`} />
        <path
          d={linha} fill="none" stroke={traco} strokeWidth="2"
          strokeLinejoin="round" strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />
      </svg>

      {/* Rótulo do alerta — HTML, não distorce */}
      {topLimite !== null && !mini && (
        <div
          style={{
            position: "absolute",
            top: Math.max(0, topLimite - 15),
            right: 6,
            fontSize: 10,
            color: "var(--red)",
            opacity: 0.75,
            background: "var(--white)",
            padding: "0 3px",
            borderRadius: 3,
            pointerEvents: "none",
          }}
        >
          alerta {limite}{unidade}
        </div>
      )}

      {/* Eixo de tempo — HTML por cima, para não distorcer com o esticão
          horizontal do SVG. Primeira marca alinhada à esquerda e última à
          direita: assim o texto não vaza da caixa. */}
      {marcas.map((m, k) => (
        <div
          key={`t${m.i}`}
          style={{
            position: "absolute",
            top: altura + 2,
            left: k === 0 ? 0 : k === marcas.length - 1 ? undefined : `${m.pctX}%`,
            right: k === marcas.length - 1 ? 0 : undefined,
            transform:
              k === 0 || k === marcas.length - 1 ? undefined : "translateX(-50%)",
            fontSize: 10,
            color: "var(--text-3)",
            whiteSpace: "nowrap",
            pointerEvents: "none",
          }}
        >
          {m.texto}
        </div>
      ))}

      {/* Ponto do valor atual — sempre na borda direita */}
      <div
        style={{
          position: "absolute",
          top: py(ultimo) - 4,
          right: 4,
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: traco,
          boxShadow: "0 0 0 2px var(--white)",
          pointerEvents: "none",
        }}
      />
    </div>
  );
}

/**
 * Paleta das séries do gráfico de containers.
 *
 * Doze cores distinguíveis entre si e legíveis nos dois temas. Acima
 * disso as cores se repetem — e é de propósito: um gráfico com mais de
 * doze linhas simultâneas não se lê, e o caminho é filtrar, não colorir
 * mais.
 */
export const PALETA = [
  "#1A6FC4", "#E4572E", "#17A398", "#8E44AD", "#F2A65A", "#2E86AB",
  "#C0392B", "#27AE60", "#D81B60", "#7F8C8D", "#5B6ABF", "#B58900",
];

export const corDaSerie = (i) => PALETA[i % PALETA.length];

/**
 * Escala vertical com marcas redondas.
 *
 * `max * 1,1` dava eixos com "3847 MB" escrito no meio — número que
 * ninguém lê e que não ajuda a comparar. Aqui o topo sobe até o próximo
 * valor redondo (1, 2 ou 5 vezes potência de dez), que é como todo
 * gráfico de operação se lê.
 */
function escalaBonita(maximo, divisoes = 4) {
  if (!isFinite(maximo) || maximo <= 0) return { topo: 1, passo: 0.25 };
  const bruto = maximo / divisoes;
  const magnitude = Math.pow(10, Math.floor(Math.log10(bruto)));
  const normalizado = bruto / magnitude;
  const passo =
    (normalizado <= 1 ? 1 : normalizado <= 2 ? 2 : normalizado <= 5 ? 5 : 10) *
    magnitude;
  return { topo: Math.ceil(maximo / passo) * passo, passo };
}

// Degraus do eixo do tempo, em milissegundos. O eixo escolhe o primeiro
// que couber em ~5 marcas — sem isso, uma janela de um ano ganharia marca
// de minuto em minuto, e uma de dez minutos, marca de dia.
const DEGRAUS_MS = [
  60e3, 5 * 60e3, 15 * 60e3, 30 * 60e3, 3600e3, 3 * 3600e3, 6 * 3600e3,
  12 * 3600e3, 86400e3, 2 * 86400e3, 7 * 86400e3, 14 * 86400e3,
  30 * 86400e3, 90 * 86400e3, 180 * 86400e3, 365 * 86400e3,
];

function marcasDeTempo(t0, t1, alvo = 5) {
  const span = Math.max(1, t1 - t0);
  const passo =
    DEGRAUS_MS.find((d) => span / d <= alvo) || DEGRAUS_MS[DEGRAUS_MS.length - 1];
  // Alinha a primeira marca ao passo, em hora local: marca em "14:00" se
  // lê; em "14:07" não.
  const inicio = Math.ceil(t0 / passo) * passo;
  const marcas = [];
  for (let t = inicio; t <= t1; t += passo) marcas.push(t);
  return { marcas, passo };
}

function rotuloDeTempo(ms, passo) {
  const d = new Date(ms);
  if (passo < 3600e3) {
    return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }
  if (passo < 86400e3) {
    // Meia-noite ganha a data: senão o dia vira quando ninguém percebe.
    return d.getHours() === 0
      ? d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })
      : d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  }
  if (passo < 30 * 86400e3) {
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  }
  return d.toLocaleDateString("pt-BR", { month: "2-digit", year: "2-digit" });
}

/**
 * Largura real do elemento, medida.
 *
 * O gráfico antigo desenhava num viewBox fixo de 800 e esticava com
 * `preserveAspectRatio="none"`. Isso deformava tudo que não fosse linha —
 * texto e círculo saíam achatados — e obrigava a pôr os rótulos como HTML
 * por cima. Medindo a largura, o SVG passa a ser desenhado no tamanho que
 * ele realmente tem, e traço, fonte e ponto ficam proporcionais em
 * qualquer tela.
 */
function useLargura(ref, padrao = 760) {
  const [largura, setLargura] = React.useState(padrao);
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const medir = () => setLargura(Math.max(320, el.clientWidth || padrao));
    medir();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", medir);
      return () => window.removeEventListener("resize", medir);
    }
    const observador = new ResizeObserver(medir);
    observador.observe(el);
    return () => observador.disconnect();
  }, [ref, padrao]);
  return largura;
}

/**
 * Várias séries num gráfico só — o "quem está comendo a RAM".
 *
 * `series` = [{nome, cor, pontos: [{ts, valor}]}]. Quem está fora de
 * `visiveis` some do desenho sem sumir da lista: esconder é filtro de
 * leitura, não exclusão de dado.
 *
 * Três diferenças em relação ao `GraficoLinha`, e as três vêm da natureza
 * do dado:
 *
 * * **eixo Y calculado, não fixo em 100%.** Aqui não há teto: o que
 *   importa é comparar containers entre si, e escala fixa achataria todo
 *   mundo contra o chão por causa do maior.
 * * **sem preenchimento de área.** Doze áreas translúcidas empilhadas
 *   viram uma mancha; linha limpa é o que deixa cruzar as curvas.
 * * **buraco de coleta não vira reta.** Quando o intervalo entre dois
 *   pontos passa de 2,5x o normal da série, a linha é interrompida — o
 *   painel esteve fora, e desenhar a reta ali inventaria uma medição que
 *   ninguém fez.
 *
 * O cursor mostra os valores daquele instante, ordenados do maior para o
 * menor: é a pergunta que se faz olhando um gráfico com muitas linhas —
 * "às 3h da manhã, quem estava por cima?".
 */
export function GraficoMultiLinha({
  series,
  altura = 260,
  unidade = " MB",
  visiveis = null,
  destaque = "",
  formatar = null,
  aoClicar = null,
}) {
  const caixa = React.useRef(null);
  const largura = useLargura(caixa);
  const [cursor, setCursor] = React.useState(null);

  const ativas = (series || []).filter(
    (s) => (!visiveis || visiveis.has(s.nome)) && s.pontos && s.pontos.length,
  );

  // Proporção: cresce com a largura até um teto, e nunca fica mais baixo
  // que o pedido. Numa tela de 1080p o gráfico ganha altura em vez de
  // virar uma tira; no celular, não estoura a dobra.
  const H = Math.round(
    Math.min(Math.max(largura * 0.34, altura), altura * 1.7),
  );
  const L = 58, R = 16, T = 14, B = 28;
  const areaW = Math.max(10, largura - L - R);
  const areaH = Math.max(10, H - T - B);

  const formatarValor =
    formatar ||
    ((v) => (v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${Math.round(v)}${unidade}`));

  if (!ativas.length) {
    return (
      <div
        ref={caixa}
        className="small muted"
        style={{
          height: altura,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
        }}
      >
        Nenhuma série selecionada.
      </div>
    );
  }

  const carimbos = ativas.flatMap((s) => s.pontos.map((p) => +new Date(p.ts)));
  const t0 = Math.min(...carimbos);
  const t1 = Math.max(...carimbos);
  const span = Math.max(1, t1 - t0);

  const pico = Math.max(...ativas.flatMap((s) => s.pontos.map((p) => p.valor)));
  const { topo, passo } = escalaBonita(pico);

  const px = (ts) => L + ((+new Date(ts) - t0) / span) * areaW;
  const py = (v) => T + areaH - (Math.min(Math.max(v, 0), topo) / topo) * areaH;

  // Cadência típica da série, para saber o que é buraco. Mediana, não
  // média: um único buraco enorme puxaria a média e esconderia os outros.
  const intervalos = [];
  const maisLonga = ativas.reduce(
    (a, b) => (b.pontos.length > a.pontos.length ? b : a),
    ativas[0],
  );
  for (let i = 1; i < maisLonga.pontos.length; i++) {
    intervalos.push(+new Date(maisLonga.pontos[i].ts) - +new Date(maisLonga.pontos[i - 1].ts));
  }
  intervalos.sort((a, b) => a - b);
  const cadencia = intervalos.length ? intervalos[Math.floor(intervalos.length / 2)] : 0;
  const limiteBuraco = cadencia > 0 ? cadencia * 2.5 : Infinity;

  const { marcas, passo: passoTempo } = marcasDeTempo(t0, t1);

  const marcasY = [];
  for (let v = 0; v <= topo + passo / 2; v += passo) marcasY.push(v);

  const caminho = (pontos) => {
    let d = "";
    let anterior = null;
    pontos.forEach((p) => {
      const atual = +new Date(p.ts);
      const salto = anterior !== null && atual - anterior > limiteBuraco;
      d += `${!d || salto ? "M" : "L"} ${px(p.ts).toFixed(1)},${py(p.valor).toFixed(1)} `;
      anterior = atual;
    });
    return d.trim();
  };

  // ── Cursor ─────────────────────────────────────────────────────────
  const aoMover = (evento) => {
    const caixaSvg = evento.currentTarget.getBoundingClientRect();
    const x = evento.clientX - caixaSvg.left;
    if (x < L || x > largura - R) return setCursor(null);
    const alvo = t0 + ((x - L) / areaW) * span;

    const leituras = ativas
      .map((s) => {
        let melhor = null;
        let distancia = Infinity;
        s.pontos.forEach((p) => {
          const d = Math.abs(+new Date(p.ts) - alvo);
          if (d < distancia) {
            distancia = d;
            melhor = p;
          }
        });
        // Ponto longe demais não é leitura daquele instante: a série
        // estava fora do ar ali, e mostrar o vizinho seria inventar.
        if (!melhor || distancia > Math.max(limiteBuraco, span / areaW * 8)) return null;
        return { nome: s.nome, cor: s.cor, valor: melhor.valor, ts: melhor.ts };
      })
      .filter(Boolean)
      .sort((a, b) => b.valor - a.valor);

    setCursor(leituras.length ? { x, alvo, leituras } : null);
  };

  const ladoDireito = cursor && cursor.x > largura * 0.55;

  return (
    <div ref={caixa} style={{ position: "relative", width: "100%" }}>
      <svg
        width={largura}
        height={H}
        viewBox={`0 0 ${largura} ${H}`}
        style={{ display: "block", cursor: aoClicar ? "pointer" : "crosshair" }}
        onMouseMove={aoMover}
        onMouseLeave={() => setCursor(null)}
        role="img"
        aria-label={`${ativas.length} série(s), pico de ${formatarValor(pico)}`}
      >
        {marcasY.map((v) => (
          <g key={`y${v}`}>
            <line
              x1={L} x2={largura - R} y1={py(v)} y2={py(v)}
              stroke="var(--border)" strokeWidth="1"
              strokeDasharray={v === 0 ? undefined : "2 4"}
            />
            <text
              x={L - 8} y={py(v) + 3} textAnchor="end"
              style={{ fontSize: 10, fill: "var(--text-3)" }}
            >
              {formatarValor(v)}
            </text>
          </g>
        ))}

        {marcas.map((ms) => (
          <g key={`x${ms}`}>
            <line
              x1={px(ms)} x2={px(ms)} y1={T} y2={T + areaH}
              stroke="var(--border)" strokeWidth="1" strokeDasharray="2 6"
              opacity="0.6"
            />
            <text
              x={px(ms)} y={H - 8} textAnchor="middle"
              style={{ fontSize: 10, fill: "var(--text-3)" }}
            >
              {rotuloDeTempo(ms, passoTempo)}
            </text>
          </g>
        ))}

        {ativas.map((s) => {
          const apagada = destaque && destaque !== s.nome;
          return (
            <path
              key={s.nome}
              d={caminho(s.pontos)}
              fill="none"
              stroke={s.cor}
              strokeWidth={destaque === s.nome ? 2.4 : 1.5}
              strokeOpacity={apagada ? 0.18 : 0.95}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          );
        })}

        {cursor && (
          <>
            <line
              x1={cursor.x} x2={cursor.x} y1={T} y2={T + areaH}
              stroke="var(--text-3)" strokeWidth="1" strokeDasharray="3 3"
            />
            {cursor.leituras.map((l) => (
              <circle
                key={l.nome}
                cx={px(l.ts)} cy={py(l.valor)} r="3"
                fill="var(--white)" stroke={l.cor} strokeWidth="2"
              />
            ))}
          </>
        )}
      </svg>

      {cursor && (
        <div
          style={{
            position: "absolute",
            top: 8,
            left: ladoDireito ? undefined : cursor.x + 14,
            right: ladoDireito ? largura - cursor.x + 14 : undefined,
            background: "var(--white)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: "6px 8px",
            fontSize: 11,
            pointerEvents: "none",
            boxShadow: "0 4px 14px rgba(0,0,0,.18)",
            maxWidth: 320,
            zIndex: 2,
          }}
        >
          <div className="muted" style={{ marginBottom: 4 }}>
            {new Date(cursor.alvo).toLocaleString("pt-BR")}
          </div>
          {cursor.leituras.slice(0, 8).map((l) => (
            <div
              key={l.nome}
              style={{ display: "flex", gap: 6, alignItems: "center", lineHeight: 1.5 }}
            >
              <span
                style={{
                  width: 8, height: 8, borderRadius: 2,
                  background: l.cor, flexShrink: 0,
                }}
              />
              <span
                className="mono"
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  flex: 1,
                }}
              >
                {l.nome.replace(/^findface-multi-/, "")}
              </span>
              <strong>{formatarValor(l.valor)}</strong>
            </div>
          ))}
          {cursor.leituras.length > 8 && (
            <div className="muted" style={{ marginTop: 3 }}>
              e mais {cursor.leituras.length - 8}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Versão miniatura, para o cartão do servidor. */
export function Faisca({ serie, limite = null, altura = 34, maximo = 100 }) {
  if (!serie || serie.length < 2) {
    return <div style={{ height: altura }} />;
  }
  return (
    <GraficoLinha
      serie={serie}
      limite={limite}
      altura={altura}
      maximo={maximo}
      rotulo="faisca"
    />
  );
}

/**
 * Barra horizontal com faixa de cor e leitura em texto.
 *
 * O texto ao lado não é redundante: cor sozinha exclui quem não distingue
 * verde de vermelho, e é justamente a informação crítica aqui.
 */
export function BarraMetrica({ rotulo, valor, limite = 90, detalhe = "", unidade = "%" }) {
  const v = Math.min(Math.max(valor || 0, 0), 100);
  const cor = corPara(v, limite);
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="stat-top" style={{ marginBottom: 3 }}>
        <span className="small" style={{ fontWeight: 500 }}>{rotulo}</span>
        <span className="small mono" style={{ color: cor, fontWeight: 600 }}>
          {valor ?? 0}{unidade}
        </span>
      </div>
      <div className="meter">
        <div
          className="meter-fill"
          style={{ width: `${v}%`, background: cor, transition: "width .4s, background .4s" }}
        />
      </div>
      {detalhe && <div className="small muted" style={{ marginTop: 2 }}>{detalhe}</div>}
    </div>
  );
}

/**
 * Toca um aviso sonoro gerado pelo próprio navegador.
 *
 * Sem arquivo de áudio: nada para baixar, nada para hospedar, e funciona
 * offline. Dois tons curtos para "atenção", três graves para "crítico" —
 * distinguíveis sem olhar a tela, que é o ponto de um alerta sonoro.
 */
export function tocarAlerta(nivel = "atencao") {
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();

    const notas = nivel === "critico"
      ? [[440, 0], [370, 0.18], [294, 0.36]]
      : [[660, 0], [880, 0.14]];

    notas.forEach(([hz, atraso]) => {
      const osc = ctx.createOscillator();
      const vol = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = hz;
      // Envelope curto: bipe seco chama atenção sem irritar quem passa o
      // dia com a tela aberta.
      vol.gain.setValueAtTime(0.0001, ctx.currentTime + atraso);
      vol.gain.exponentialRampToValueAtTime(0.12, ctx.currentTime + atraso + 0.02);
      vol.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + atraso + 0.16);
      osc.connect(vol);
      vol.connect(ctx.destination);
      osc.start(ctx.currentTime + atraso);
      osc.stop(ctx.currentTime + atraso + 0.18);
    });

    setTimeout(() => ctx.close().catch(() => {}), 1200);
  } catch {
    // Navegador que bloqueia áudio sem interação não pode quebrar a tela
  }
}
