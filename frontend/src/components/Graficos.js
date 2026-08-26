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

  return (
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

      {/* Grade a 25/50/75% — referência sem poluir */}
      {[25, 50, 75].map((v) => (
        <line
          key={v}
          x1={L} x2={W - R}
          y1={py((v / 100) * maximo)} y2={py((v / 100) * maximo)}
          stroke="var(--border)" strokeWidth="1" strokeDasharray="2 4"
        />
      ))}

      {limite !== null && limite <= maximo && (
        <>
          <line
            x1={L} x2={W - R} y1={py(limite)} y2={py(limite)}
            stroke="var(--red)" strokeWidth="1.5" strokeDasharray="5 3"
            opacity="0.55"
          />
          <text
            x={W - R} y={py(limite) - 4} textAnchor="end"
            fontSize="10" fill="var(--red)" opacity="0.8"
          >
            alerta {limite}{unidade}
          </text>
        </>
      )}

      <path d={area} fill={`url(#${idGrad})`} />
      <path
        d={linha} fill="none" stroke={traco} strokeWidth="2"
        strokeLinejoin="round" strokeLinecap="round"
      />
      <circle cx={px(n - 1)} cy={py(ultimo)} r="3.5" fill={traco} />
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
