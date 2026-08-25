import React, { useCallback, useEffect, useState } from "react";
import { api, clearToken, getToken } from "./api";
import AppShell from "./pages/AppShell";
import Login from "./pages/Login";
import { carregarMarca } from "./marca";
import { SessaoContext } from "./usePermissions";

export default function App() {
  const [sessao, setSessao] = useState({ usuario: null, permissoes: [] });
  const [carregando, setCarregando] = useState(true);
  const [marca, setMarca] = useState(null);

  // Antes de qualquer tela: cores e logo do cliente. Aplicar depois faria
  // a paleta padrão piscar na frente do usuário.
  useEffect(() => {
    carregarMarca().then(setMarca);
  }, []);

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

  const sair = useCallback(async () => {
    // Avisa o servidor ANTES de limpar: ele incrementa a versão do token,
    // o que invalida qualquer cópia que já tenha sido feita. Só apagar do
    // navegador deixaria um token roubado valendo até expirar.
    try {
      await api.sair();
    } catch {
      // Servidor fora do ar não pode impedir o usuário de sair da tela
    }
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
    return <Login onEntrar={carregar} marca={marca} />;
  }

  return (
    <SessaoContext.Provider
      value={{ ...sessao, recarregar: carregar, sair, marca }}
    >
      <AppShell />
    </SessaoContext.Provider>
  );
}
