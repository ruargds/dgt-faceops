import React, { useCallback, useEffect, useState } from "react";
import { api, clearToken, getToken } from "./api";
import AppShell from "./pages/AppShell";
import Login from "./pages/Login";
import { SessaoContext } from "./usePermissions";

export default function App() {
  const [sessao, setSessao] = useState({ usuario: null, permissoes: [] });
  const [carregando, setCarregando] = useState(true);

  const carregar = useCallback(async () => {
    if (!getToken()) {
      setSessao({ usuario: null, permissoes: [] });
      setCarregando(false);
      return;
    }
    try {
      const dados = await api.me();
      setSessao({ usuario: dados.usuario, permissoes: dados.permissoes });
    } catch {
      // Token velho ou inválido — volta para o login sem drama
      clearToken();
      setSessao({ usuario: null, permissoes: [] });
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  const sair = useCallback(() => {
    clearToken();
    setSessao({ usuario: null, permissoes: [] });
  }, []);

  if (carregando) {
    return (
      <div className="login-bg">
        <div className="spin" style={{ borderTopColor: "#fff" }} />
      </div>
    );
  }

  if (!sessao.usuario) {
    return <Login onEntrar={carregar} />;
  }

  return (
    <SessaoContext.Provider
      value={{ ...sessao, recarregar: carregar, sair }}
    >
      <AppShell />
    </SessaoContext.Provider>
  );
}
