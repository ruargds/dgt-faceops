import React, { useState } from "react";
import { api, setToken } from "../api";

export default function Login({ onEntrar }) {
  const [usuario, setUsuario] = useState("");
  const [senha, setSenha] = useState("");
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
      <form className="login-card" onSubmit={enviar}>
        <img className="login-logo" src="/logos/dgt-login.png" alt="DGT" />
        <div className="login-sub">FaceOps — operação do FindFace Multi</div>

        {erro && <div className="login-err">{erro}</div>}

        <div className="field">
          <label className="label" htmlFor="usuario">Usuário</label>
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
          <label className="label" htmlFor="senha">Senha</label>
          <input
            id="senha"
            type="password"
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
            autoComplete="current-password"
            required
          />
        </div>

        <button
          className="btn btn-primary"
          style={{ width: "100%", justifyContent: "center", padding: "10px" }}
          disabled={enviando}
        >
          {enviando ? "Entrando…" : "Entrar"}
        </button>

        <div className="login-hint">
          Primeiro acesso: <strong>admin</strong> / <strong>admin123</strong>
          <br />
          Troque a senha logo após entrar.
        </div>
      </form>
    </div>
  );
}
