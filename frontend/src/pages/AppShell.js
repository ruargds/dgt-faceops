import React, { useState } from "react";
import { api } from "../api";
import {
  IconAgenda,
  IconAuditoria,
  IconAlerta,
  IconBackup,
  IconChave,
  IconPainel,
  IconRecursos,
  IconSair,
  IconServicos,
  IconServidor,
  IconLogs,
  IconTerminal,
  IconUsuarios,
} from "../components/Icons";
import AgendamentosView from "../components/views/AgendamentosView";
import ConfiguracoesView from "../components/views/ConfiguracoesView";
import DestinosView from "../components/views/DestinosView";
import LogsView from "../components/views/LogsView";
import ManutencaoView from "../components/views/ManutencaoView";
import AuditoriaView from "../components/views/AuditoriaView";
import BackupsView from "../components/views/BackupsView";
import PainelView from "../components/views/PainelView";
import RecursosView from "../components/views/RecursosView";
import ServicosView from "../components/views/ServicosView";
import ServidoresView from "../components/views/ServidoresView";
import TerminalView from "../components/views/TerminalView";
import UsuariosView from "../components/views/UsuariosView";
import { useSessao, usePermissions } from "../usePermissions";

const MENU = [
  { grupo: "Operação" },
  { id: "painel", rotulo: "Painel", icone: IconPainel, perm: "hosts.view" },
  { id: "recursos", rotulo: "Recursos", icone: IconRecursos, perm: "metrics.view" },
  { id: "servicos", rotulo: "Serviços", icone: IconServicos, perm: "services.view" },
  { id: "logs", rotulo: "Logs ao vivo", icone: IconLogs, perm: "services.view" },
  { id: "manutencao", rotulo: "Manutenção", icone: IconAlerta, perm: "maintenance.view" },
  { id: "terminal", rotulo: "InTerminal", icone: IconTerminal, perm: "terminal.use" },

  { grupo: "Backup" },
  { id: "backups", rotulo: "Backups", icone: IconBackup, perm: "backups.view" },
  { id: "agendamentos", rotulo: "Agendamentos", icone: IconAgenda, perm: "schedules.view" },
  { id: "destinos", rotulo: "Destinos", icone: IconChave, perm: "backups.view" },

  { grupo: "Administração" },
  { id: "servidores", rotulo: "Servidores", icone: IconServidor, perm: "hosts.view" },
  { id: "usuarios", rotulo: "Usuários", icone: IconUsuarios, perm: "users.manage" },
  { id: "auditoria", rotulo: "Auditoria", icone: IconAuditoria, perm: "audit.view" },
  { id: "config", rotulo: "Configurações", icone: IconServicos, perm: "hosts.view" },
];

export default function AppShell() {
  const { usuario, sair, recarregar } = useSessao();
  const { has } = usePermissions();
  const [aba, setAba] = useState("painel");
  const [trocandoSenha, setTrocandoSenha] = useState(false);

  // Monta o menu escondendo o que o perfil não pode ver. Um cabeçalho de
  // grupo só aparece se sobrou pelo menos um item embaixo dele — título
  // solto sobre espaço vazio parece defeito da tela.
  const itens = [];
  MENU.forEach((item, i) => {
    if (!item.grupo) {
      if (has(item.perm)) itens.push(item);
      return;
    }
    const filhos = [];
    for (let j = i + 1; j < MENU.length && !MENU[j].grupo; j++) filhos.push(MENU[j]);
    if (filhos.some((f) => has(f.perm))) itens.push(item);
  });

  const primeira = itens.find((i) => i.id);
  const abaValida = itens.some((i) => i.id === aba) ? aba : primeira && primeira.id;

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <img src="/logos/dgt-sidebar.png" alt="DGT FaceOps" />
        </div>

        <nav className="sidebar-nav">
          {itens.map((item, idx) =>
            item.grupo ? (
              <div className="nav-group" key={`g${idx}`}>{item.grupo}</div>
            ) : (
              <button
                key={item.id}
                className={`nav-item ${abaValida === item.id ? "active" : ""}`}
                onClick={() => setAba(item.id)}
              >
                <item.icone size={17} />
                {item.rotulo}
              </button>
            )
          )}
        </nav>

        <div className="sidebar-foot">
          <div className="sidebar-user">{usuario.full_name || usuario.username}</div>
          <div className="sidebar-role">{usuario.role}</div>
          <button
            className="btn btn-ghost btn-sm"
            style={{ color: "#A9B8CC", padding: "4px 0", marginTop: 6 }}
            onClick={sair}
          >
            <IconSair size={15} /> Sair
          </button>
        </div>
      </aside>

      <main className="main">
        {usuario.senha_padrao && (
          <div className="banner banner-warn">
            Este usuário ainda está com a senha de fábrica (<strong>admin123</strong>).
            Qualquer pessoa com acesso à rede consegue entrar no painel e nos servidores.
            <button className="btn btn-secondary btn-sm" onClick={() => setTrocandoSenha(true)}>
              Trocar senha
            </button>
          </div>
        )}

        <div className="content">
          {abaValida === "painel" && <PainelView />}
          {abaValida === "recursos" && <RecursosView />}
          {abaValida === "servicos" && <ServicosView />}
          {abaValida === "logs" && <LogsView />}
          {abaValida === "manutencao" && <ManutencaoView />}
          {abaValida === "terminal" && <TerminalView />}
          {abaValida === "backups" && <BackupsView />}
          {abaValida === "agendamentos" && <AgendamentosView />}
          {abaValida === "destinos" && <DestinosView />}
          {abaValida === "servidores" && <ServidoresView />}
          {abaValida === "usuarios" && <UsuariosView />}
          {abaValida === "auditoria" && <AuditoriaView />}
          {abaValida === "config" && <ConfiguracoesView />}
        </div>
      </main>

      {trocandoSenha && (
        <ModalTrocarSenha
          onFechar={() => setTrocandoSenha(false)}
          onPronto={async () => {
            setTrocandoSenha(false);
            await recarregar();
          }}
        />
      )}
    </div>
  );
}

function ModalTrocarSenha({ onFechar, onPronto }) {
  const [atual, setAtual] = useState("");
  const [nova, setNova] = useState("");
  const [confirmar, setConfirmar] = useState("");
  const [erro, setErro] = useState("");
  const [enviando, setEnviando] = useState(false);

  async function enviar(e) {
    e.preventDefault();
    if (nova !== confirmar) {
      setErro("A confirmação não confere com a senha nova.");
      return;
    }
    setErro("");
    setEnviando(true);
    try {
      await api.trocarSenha(atual, nova);
      await onPronto();
    } catch (ex) {
      setErro(ex.message);
      setEnviando(false);
    }
  }

  return (
    <div className="modal-bg" onClick={onFechar}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={enviar}>
        <div className="modal-head">
          <div className="modal-title">Trocar senha</div>
        </div>
        <div className="modal-body">
          {erro && <div className="login-err">{erro}</div>}
          <div className="field">
            <label className="label label-required">Senha atual</label>
            <input type="password" value={atual} onChange={(e) => setAtual(e.target.value)} required autoFocus />
          </div>
          <div className="field">
            <label className="label label-required">Senha nova</label>
            <input type="password" value={nova} onChange={(e) => setNova(e.target.value)} required minLength={6} />
            <div className="field-help">Mínimo de 6 caracteres.</div>
          </div>
          <div className="field">
            <label className="label label-required">Confirmar senha nova</label>
            <input type="password" value={confirmar} onChange={(e) => setConfirmar(e.target.value)} required />
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>Cancelar</button>
          <button className="btn btn-primary" disabled={enviando}>
            {enviando ? "Salvando…" : "Trocar senha"}
          </button>
        </div>
      </form>
    </div>
  );
}
