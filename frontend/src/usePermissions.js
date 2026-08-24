import { createContext, useContext } from "react";

export const SessaoContext = createContext({
  usuario: null,
  permissoes: [],
  recarregar: () => {},
  sair: () => {},
});

/**
 * Hook de permissões.
 *
 * Regra da UI (herdada do InfraCore): botão sem permissão é OMITIDO, não
 * desabilitado. Botão cinza que não faz nada gera chamado de suporte;
 * botão ausente não gera dúvida.
 */
export function usePermissions() {
  const { permissoes } = useContext(SessaoContext);
  const has = (codigo) => permissoes.includes(codigo);
  const hasAny = (...codigos) => codigos.some((c) => permissoes.includes(c));
  return { has, hasAny, permissoes };
}

export function useSessao() {
  return useContext(SessaoContext);
}
