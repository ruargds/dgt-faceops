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
 * Várias séries num gráfico só — o "quem está comendo a RAM".
 *
 * `series` = [{nome, cor, pontos: [{ts, valor}]}]. Cada série desenha uma
 * linha; quem está fora de `visiveis` some do desenho sem sumir da lista,
 * porque esconder é filtro de leitura, não exclusão de dado.
 *
 * Duas diferenças em relação ao `GraficoLinha`, e as duas vêm da natureza
 * do dado:
 *
 * * **eixo Y em MB, com máximo calculado.** Aqui não há teto de 100%: o
 *   que importa é comparar containers entre si, e uma escala fixa
 *   achataria todo mundo contra o chão por causa do maior.
 * * **sem preenchimento de área.** Doze áreas translúcidas empilhadas
 *   viram uma mancha; linha limpa é o que deixa cruzar as curvas.
 *
 * O eixo do tempo é comum a todas: cada série pode ter começado em
 * momento diferente (container que subiu depois), então a posição
 * horizontal vem do carimbo, não do índice do ponto.
 */
export function GraficoMultiLinha({
  series,
  altura = 220,
  unidade = " MB",
  visiveis = null,
  destaque = "",
  formatar = null,
}) {
  const L = 44, R = 10, T = 10, B = 20;
  const W = 800;
  const H = altura;
  const areaW = W - L - R;
  const areaH = H - T - B;

  const ativas = (series || []).filter(
    (s) => (!visiveis || visiveis.has(s.nome)) && s.pontos && s.pontos.length,
  );

  if (!ativas.length) {
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
        Nenhuma série selecionada.
      </div>
    );
  }

  const carimbos = ativas.flatMap((s) =>
    s.pontos.map((p) => new Date(p.ts).getTime()),
  );
  const t0 = Math.min(...carimbos);
  const t1 = Math.max(...carimbos);
  const spanMs = Math.max(1, t1 - t0);

  const topo = Math.max(...ativas.flatMap((s) => s.pontos.map((p) => p.valor)));
  // Uma folga de 10% no topo evita a linha do maior colar na borda, onde
  // ela some contra o quadro.
  const maximo = topo > 0 ? topo * 1.1 : 1;

  const px = (ts) => L + ((new Date(ts).getTime() - t0) / spanMs) * areaW;
  const py = (v) => T + areaH - (Math.min(Math.max(v, 0), maximo) / maximo) * areaH;

  // O formatador padrão é o de memória (MB que vira GB). CPU e outras
  // unidades passam o seu — sem isso, "42%" viraria "0.0 GB" no eixo.
  const formatarValor =
    formatar ||
    ((v) => (v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${Math.round(v)}${unidade}`));

  const formatarHora = (ms) => {
    const d = new Date(ms);
    if (spanMs <= 36 * 3600e3) {
      return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
  };

  const marcasY = [0, 0.25, 0.5, 0.75, 1].map((f) => ({
    y: py(maximo * f),
    texto: formatarValor(maximo * f),
  }));

  const marcasX = [0, 0.5, 1].map((f) => ({
    x: L + f * areaW,
    texto: formatarHora(t0 + f * spanMs),
    ancora: f === 0 ? "start" : f === 1 ? "end" : "middle",
  }));

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      style={{ width: "100%", height: altura, display: "block" }}
      role="img"
      aria-label={`Memória por container: ${ativas.length} série(s)`}
    >
      {marcasY.map((m, i) => (
        <g key={`y${i}`}>
          <line
            x1={L} x2={W - R} y1={m.y} y2={m.y}
            stroke="var(--border)" strokeWidth="1" strokeDasharray="2 4"
          />
          <text
            x={L - 6} y={m.y + 3} textAnchor="end"
            style={{ fontSize: 9, fill: "var(--text-3)" }}
          >
            {m.texto}
          </text>
        </g>
      ))}

      {marcasX.map((m, i) => (
        <text
          key={`x${i}`}
          x={m.x} y={H - 5} textAnchor={m.ancora}
          style={{ fontSize: 9, fill: "var(--text-3)" }}
        >
          {m.texto}
        </text>
      ))}

      {ativas.map((s) => {
        const d = s.pontos
          .map((p, i) => `${i === 0 ? "M" : "L"} ${px(p.ts).toFixed(1)},${py(p.valor).toFixed(1)}`)
          .join(" ");
        // Com uma série em destaque, as outras recuam em vez de sumir: o
        // ponto de ter tudo no mesmo gráfico é comparar, e comparação
        // precisa das vizinhas visíveis.
        const apagada = destaque && destaque !== s.nome;
        return (
          <path
            key={s.nome}
            d={d}
            fill="none"
            stroke={s.cor}
            strokeWidth={destaque === s.nome ? 2.6 : 1.6}
            strokeOpacity={apagada ? 0.25 : 1}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        );
      })}
    </svg>
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
