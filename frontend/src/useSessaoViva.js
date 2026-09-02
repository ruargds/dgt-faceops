import { useCallback, useEffect, useRef, useState } from "react";
import { api, getToken, setToken } from "./api";

/**
 * Sessão que cai por inatividade e tem teto absoluto.
 *
 * Duas regras, e a diferença entre elas importa:
 *
 * * **inatividade (20 min)** — sem interação de GENTE, a sessão cai. É o
 *   que protege a estação esquecida aberta.
 * * **teto absoluto (24 h)** — contado do login, não se estende. É o que
 *   impede uma sessão de se renovar para sempre.
 *
 * A armadilha que este arquivo existe para evitar: **o painel se atualiza
 * sozinho a cada 10 s.** Se a renovação acontecesse a cada requisição, o
 * próprio polling seguraria a sessão viva indefinidamente, e o tempo de
 * inatividade nunca chegaria ao fim. Por isso a renovação depende de
 * evento de entrada do navegador, e não de tráfego de rede.
 *
 * Quais eventos contam: clique, tecla, rolagem e toque. **`mousemove`
 * ficou de fora de propósito** — mesa esbarrada, tela em parede e mouse
 * com deriva mantêm uma sessão privilegiada aberta sem ninguém ali.
 */

// Eventos que provam que há alguém. Passivos, para não custar rolagem.
const EVENTOS = ["pointerdown", "keydown", "wheel", "touchstart"];

// Ninguém precisa registrar interação a cada tecla: um carimbo a cada
// 15 s basta para a conta e não gera trabalho à toa.
const PASSO_REGISTRO_MS = 15000;

// De quanto em quanto tempo a regra é conferida.
const PASSO_CHECAGEM_MS = 20000;

// Renova quando falta menos que isto para o token expirar. Cinco minutos
// dão folga para uma renovação falhar e ser tentada de novo.
const MARGEM_RENOVACAO_S = 5 * 60;

// Aviso antes de derrubar. Encerrar sem avisar perde o que estava sendo
// digitado, e quem perde formulário desconfia do painel inteiro.
const AVISO_ANTES_S = 60;

/** Lê o `exp` do token sem verificar assinatura — só para saber o prazo. */
export function prazoDoToken(token) {
  try {
    const corpo = JSON.parse(atob(token.split(".")[1]));
    return { exp: corpo.exp || 0, ini: corpo.ini || 0 };
  } catch {
    return { exp: 0, ini: 0 };
  }
}

export function useSessaoViva({ logado, aoEncerrar }) {
  const [politica, setPolitica] = useState({ inatividade_min: 20, maxima_h: 24 });
  const [avisando, setAvisando] = useState(0); // segundos restantes, 0 = sem aviso

  const ultimaInteracao = useRef(Date.now());
  const houveInteracao = useRef(false);
  const renovando = useRef(false);

  // Os prazos vêm do servidor: repetir os números aqui faria a tela
  // discordar da configuração no primeiro ajuste.
  useEffect(() => {
    if (!logado) return;
    api.politicaSessao().then(setPolitica).catch(() => {});
  }, [logado]);

  const marcar = useCallback(() => {
    const agora = Date.now();
    if (agora - ultimaInteracao.current < PASSO_REGISTRO_MS) return;
    ultimaInteracao.current = agora;
    houveInteracao.current = true;
    setAvisando(0);
  }, []);

  /** Chamado pelo botão "continuar conectado" do aviso. */
  const continuar = useCallback(async () => {
    ultimaInteracao.current = Date.now();
    houveInteracao.current = true;
    setAvisando(0);
    try {
      const r = await api.renovarSessao();
      if (r && r.access_token) setToken(r.access_token);
    } catch (ex) {
      aoEncerrar(ex.message || "Não foi possível renovar a sessão.");
    }
  }, [aoEncerrar]);

  useEffect(() => {
    if (!logado) return undefined;

    for (const nome of EVENTOS) {
      window.addEventListener(nome, marcar, { passive: true });
    }

    const timer = setInterval(async () => {
      const token = getToken();
      if (!token) return;

      const paradoS = (Date.now() - ultimaInteracao.current) / 1000;
      const limiteS = politica.inatividade_min * 60;

      // 1) Passou do tempo parado: encerra.
      if (paradoS >= limiteS) {
        aoEncerrar(
          `Sessão encerrada por inatividade (${politica.inatividade_min} min sem uso).`,
        );
        return;
      }

      // 2) Perto do fim: avisa, com botão para continuar.
      const faltaS = limiteS - paradoS;
      if (faltaS <= AVISO_ANTES_S) {
        setAvisando(Math.ceil(faltaS));
        return;
      }
      setAvisando(0);

      // 3) Houve gente e o token está para vencer: renova.
      const { exp } = prazoDoToken(token);
      const restaToken = exp - Date.now() / 1000;
      if (houveInteracao.current && restaToken <= MARGEM_RENOVACAO_S && !renovando.current) {
        renovando.current = true;
        try {
          const r = await api.renovarSessao();
          if (r && r.access_token) {
            setToken(r.access_token);
            houveInteracao.current = false;
          }
        } catch (ex) {
          // 401 aqui é o teto de 24 h batendo. Encerrar com a mensagem
          // do servidor é melhor que um 401 genérico na próxima tela.
          if (ex && ex.status === 401) {
            aoEncerrar(ex.message || "Sessão expirada. Entre novamente.");
          }
        } finally {
          renovando.current = false;
        }
      }
    }, PASSO_CHECAGEM_MS);

    return () => {
      clearInterval(timer);
      for (const nome of EVENTOS) window.removeEventListener(nome, marcar);
    };
  }, [logado, politica, marcar, aoEncerrar]);

  return { avisando, continuar, politica };
}
