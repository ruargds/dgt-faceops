import React, { useState } from "react";
import { api, setToken } from "../api";
import { MARCA_PADRAO, urlLogo } from "../marca";
import { t } from "../i18n";
import { GatilhoDeAutoria } from "../components/SobreOSistema";
import { IconOlho, IconOlhoCortado } from "../components/Icons";

/**
 * Tela de entrada.
 *
 * Estrutura herdada do InfraCore: o logo do cliente fica **acima** do
 * cartão, o nome do produto vem logo abaixo em tipo leve e espaçado, e o
 * formulário ocupa o cartão sozinho. Ganha-se hierarquia — marca, produto,
 * ação — em vez de empilhar tudo dentro da mesma caixa.
 *
 * A superfície escura vem do Camsync, que é o padrão DGT para tela
 * operacional; aqui ela já era o fundo, e agora o cartão acompanha em vez
 * de ser uma ilha branca.
 *
 * O rodapé pertence à marca de quem USA o painel. Quem o DESENVOLVEU fica
 * a cinco cliques dali — ver components/SobreOSistema.js.
 */
export default function Login({ onEntrar, marca, aviso }) {
  const m = marca || MARCA_PADRAO;
  const [usuario, setUsuario] = useState("");
  const [senha, setSenha] = useState("");
  const [verSenha, setVerSenha] = useState(false);
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  async function enviar(e) {
    e.preventDefault();
    setErro("");
    setEnviando(true);
    try {
      const resposta = await api.login(usuario.trim(), senha);
      setToken(resposta.access_token);
      await onEntrar();
    } catch (ex) {
      setErro(ex.message || "Não foi possível entrar.");
      setEnviando(false);
    }
  }

  return (
    <div className="login-bg">
      <div className="login-wrap">
        {/* Marca do cliente, fora do cartão. */}
        <div className="login-marca">
          <img
            className="login-logo"
            src={urlLogo(m.logos, "login", "/logos/dgt-login.png")}
            alt={m.nome}
          />
          <div className="login-produto">{m.nome}</div>
          <div className="login-sub">
            {m.subtitulo}
            {m.cliente && (
              <>
                <br />
                <strong>{m.cliente}</strong>
              </>
            )}
          </div>
        </div>

        <form className="login-card" onSubmit={enviar}>
          {/* Por que a sessão caiu. Voltar ao login sem explicação faz a
              pessoa achar que o painel quebrou — e tentar de novo no mesmo
              minuto, achando que foi falha. */}
          {aviso && !erro && (
            <div className="login-err" style={{ background: "var(--amber-bg)", borderColor: "var(--amber-bd)", color: "var(--amber-fg)" }}>
              {aviso}
            </div>
          )}
          {erro && <div className="login-err">{erro}</div>}

          <div className="field">
            <label className="label" htmlFor="usuario">{t("login.usuario")}</label>
            <input
              id="usuario"
              value={usuario}
              onChange={(e) => setUsuario(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </div>

          <div className="field">
            <label className="label" htmlFor="senha">{t("login.senha")}</label>
            {/* Ver a senha evita o erro mais comum de primeiro acesso:
                senha temporária longa digitada errada, sem saber onde. */}
            <div className="login-senha">
              <input
                id="senha"
                type={verSenha ? "text" : "password"}
                value={senha}
                onChange={(e) => setSenha(e.target.value)}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                className="login-olho"
                onClick={() => setVerSenha((v) => !v)}
                aria-label={verSenha ? t("Ocultar senha") : t("Mostrar senha")}
                title={verSenha ? t("Ocultar senha") : t("Mostrar senha")}
                tabIndex={-1}
              >
                {verSenha ? <IconOlhoCortado size={17} /> : <IconOlho size={17} />}
              </button>
            </div>
          </div>

          <button
            className="btn btn-primary login-entrar"
            disabled={enviando}
          >
            {enviando ? t("login.entrando") : t("login.entrar")}
          </button>

          <div className="login-hint">
            {t("Use as credenciais fornecidas pelo administrador.")}
            <br />
            {t("Troque a senha logo após o primeiro acesso.")}
          </div>
        </form>

        <GatilhoDeAutoria style={{ textAlign: "center", marginTop: 20 }}>
          <span className="login-rodape">
            {m.cliente ? `${m.nome} — ${m.cliente}` : m.nome}
          </span>
        </GatilhoDeAutoria>
      </div>
    </div>
  );
}
