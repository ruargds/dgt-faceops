import React, { useEffect, useState } from "react";
import { api, setToken } from "../api";
import {
  IconAgenda,
  IconAuditoria,
  IconAlerta,
  IconBackup,
  IconChave,
  IconPainel,
  IconRecursos,
  IconSair,
  IconSol,
  IconLua,
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
import MonitorView from "../components/views/MonitorView";
import DescobertaView from "../components/views/DescobertaView";
import TopologiaView from "../components/views/TopologiaView";
import DispositivosView from "../components/views/DispositivosView";
import ProcessosView from "../components/views/ProcessosView";
import RastreioView from "../components/views/RastreioView";
import ManutencaoView from "../components/views/ManutencaoView";
import AuditoriaView from "../components/views/AuditoriaView";
import BackupsView from "../components/views/BackupsView";
import PainelView from "../components/views/PainelView";
import RecursosView from "../components/views/RecursosView";
import ServicosView from "../components/views/ServicosView";
import ServidoresView from "../components/views/ServidoresView";
import TerminalView from "../components/views/TerminalView";
import UsuariosView from "../components/views/UsuariosView";
import { fecharSeForaLimpo } from "../components/Comuns";
import { MARCA_PADRAO, urlLogo } from "../marca";
import { useSessao, usePermissions } from "../usePermissions";
import { alternarTema, temaAtual } from "../tema";
import { NOMES_IDIOMA, IDIOMAS, definirIdioma, idiomaAtual, t } from "../i18n";

// O rótulo sai do dicionário (i18n.js) na hora de montar o menu — a
// tradução de menu é o mínimo para "escolher o idioma" significar alguma
// coisa, e é onde a mão de quem opera passa o dia.
const MENU = [
  { grupo: "menu.operacao" },
  { id: "painel", chave: "menu.painel", icone: IconPainel, perm: "hosts.view" },
  { id: "rastreio", chave: "menu.rastreio", icone: IconAlerta, perm: "metrics.view" },
  { id: "monitor", chave: "menu.monitor", icone: IconRecursos, perm: "metrics.view" },
  { id: "recursos", chave: "menu.recursos", icone: IconRecursos, perm: "metrics.view" },
  { id: "processos", chave: "menu.processos", icone: IconRecursos, perm: "metrics.view" },
  { id: "servicos", chave: "menu.servicos", icone: IconServicos, perm: "services.view" },
  { id: "dispositivos", chave: "menu.cameras", icone: IconServidor, perm: "metrics.view" },
  { id: "descoberta", chave: "menu.descoberta", icone: IconServidor, perm: "hosts.view" },
  { id: "topologia", chave: "menu.topologia", icone: IconServidor, perm: "hosts.view" },
  { id: "logs", chave: "menu.logs", icone: IconLogs, perm: "services.view" },
  { id: "manutencao", chave: "menu.manutencao", icone: IconAlerta, perm: "maintenance.view" },
  { id: "terminal", chave: "menu.terminal", icone: IconTerminal, perm: "terminal.use" },

  { grupo: "menu.backup" },
  { id: "backups", chave: "menu.backups", icone: IconBackup, perm: "backups.view" },
  { id: "agendamentos", chave: "menu.agendamentos", icone: IconAgenda, perm: "schedules.view" },
  { id: "destinos", chave: "menu.destinos", icone: IconChave, perm: "backups.view" },

  { grupo: "menu.administracao" },
  { id: "servidores", chave: "menu.servidores", icone: IconServidor, perm: "hosts.view" },
  { id: "usuarios", chave: "menu.usuarios", icone: IconUsuarios, perm: "users.manage" },
  { id: "auditoria", chave: "menu.auditoria", icone: IconAuditoria, perm: "audit.view" },
  { id: "config", chave: "menu.config", icone: IconServicos, perm: "hosts.view" },
];

export default function AppShell() {
  const { usuario, sair, recarregar, marca } = useSessao();
  const m = marca || MARCA_PADRAO;
  const { has } = usePermissions();
  const [aba, setAba] = useState("painel");
  const [trocandoSenha, setTrocandoSenha] = useState(false);
  const [saude, setSaude] = useState(null);
  const [tema, setTema] = useState(temaAtual);
  const [idioma, setIdioma] = useState(idiomaAtual);

  // Leitura única, na montagem. O rodapé responde "qual versão está no
  // ar?" e nada nele muda enquanto a tela está aberta — intervalo aqui
  // seria consulta de graça (regra 24 das regras de desenvolvimento).
  useEffect(() => {
    let vivo = true;
    api
      .saude()
      .then((s) => {
        if (vivo) setSaude(s);
      })
      .catch(() => {
        // Rodapé sem versão é cosmético; erro aqui não vira alerta na tela.
      });
    return () => {
      vivo = false;
    };
  }, []);

  // Selo do bundle que ESTE navegador carregou, carimbado no build pelo
  // deploy.sh. Serve a um caso só: index.html em cache apontando para o
  // bundle antigo, que faz "corrigi / não resolveu" render várias voltas.
  // Se o commit do bundle não bate com o do servidor, a tela diz isso em
  // vez de deixar a pessoa investigar o backend.
  const seloBundle = (process.env.REACT_APP_BUILD_STAMP || "").split(" ")[0];
  const revisao = saude && saude.revisao;
  const bundleDefasado = Boolean(
    seloBundle &&
      seloBundle !== "desenvolvimento" &&
      revisao &&
      revisao !== "desconhecida" &&
      revisao !== seloBundle
  );

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
          <img src={urlLogo(m.logos, "sidebar", "/logos/dgt-sidebar.png")} alt={m.nome} />
        </div>

        <nav className="sidebar-nav">
          {itens.map((item, idx) =>
            item.grupo ? (
              <div className="nav-group" key={`g${idx}`}>{t(item.grupo)}</div>
            ) : (
              <button
                key={item.id}
                className={`nav-item ${abaValida === item.id ? "active" : ""}`}
                onClick={() => setAba(item.id)}
              >
                <item.icone size={17} />
                {t(item.chave)}
              </button>
            )
          )}
        </nav>

        <div className="sidebar-foot">
          <div className="sidebar-user">{usuario.full_name || usuario.username}</div>
          <div className="sidebar-role">{usuario.role}</div>
          {/* Tema é preferência de quem olha a tela, então fica ao lado do
              usuário e vale só para este navegador — não é configuração da
              instalação. */}
          <button
            className="btn btn-ghost btn-sm"
            style={{ color: "#A9B8CC", padding: "4px 0", marginTop: 6 }}
            onClick={() => setTema(alternarTema())}
            title={
              tema === "escuro" ? t("rodape.trocar_claro") : t("rodape.trocar_escuro")
            }
          >
            {tema === "escuro" ? <IconSol size={15} /> : <IconLua size={15} />}
            {tema === "escuro" ? t("rodape.tema_claro") : t("rodape.tema_escuro")}
          </button>

          {/* Idioma: recarrega a página ao trocar, de propósito — parte das
              telas ainda tem texto literal, e meia tradução na tela é pior
              que a pausa de meio segundo. A escolha fica salva no navegador. */}
          <select
            className="seletor-idioma"
            value={idioma}
            title={t("rodape.idioma")}
            onChange={(e) => {
              setIdioma(e.target.value);
              definirIdioma(e.target.value);
            }}
          >
            {IDIOMAS.map((sigla) => (
              <option key={sigla} value={sigla}>
                {NOMES_IDIOMA[sigla]}
              </option>
            ))}
          </select>

          <button
            className="btn btn-ghost btn-sm"
            style={{ color: "#A9B8CC", padding: "4px 0", marginTop: 2 }}
            onClick={sair}
          >
            <IconSair size={15} /> {t("rodape.sair")}
          </button>

          {/* SELO DA VERSÃO — herdado do InfraCore. "Qual versão está no
              ar?" respondido de memória é a origem de meia hora de
              confusão em qualquer incidente, e até aqui a resposta só
              existia no `curl /api/saude` da VM ou no fim do
              atualizar.sh. Quem está com o painel aberto não tem nenhum
              dos dois à mão. */}
          <div
            className="sidebar-versao"
            title={t("rodape.versao")}
          >
            {saude ? `v${saude.versao} · ${saude.revisao}` : "—"}
          </div>

          {bundleDefasado && (
            <div
              className="sidebar-versao sidebar-versao-alerta"
              title={`Este navegador carregou o bundle ${seloBundle}, mas o servidor está em ${revisao}. É cache do index.html — recarregue com Ctrl+F5 antes de investigar qualquer outra coisa.`}
            >
              bundle {seloBundle} — {t("rodape.bundle_defasado")}
            </div>
          )}
        </div>
      </aside>

      <main className="main">
        {usuario.senha_padrao && (
          <div className="banner banner-warn">
            {t("senha.aviso_1")} <strong>{t("senha.aviso_forte")}</strong>.{" "}
            {t("senha.aviso_2")}
            <button className="btn btn-secondary btn-sm" onClick={() => setTrocandoSenha(true)}>
              {t("senha.trocar")}
            </button>
          </div>
        )}

        <div className="content">
          {abaValida === "painel" && <PainelView />}
          {abaValida === "rastreio" && <RastreioView />}
          {abaValida === "monitor" && <MonitorView />}
          {abaValida === "dispositivos" && <DispositivosView />}
          {abaValida === "descoberta" && <DescobertaView />}
          {abaValida === "topologia" && <TopologiaView />}
          {abaValida === "recursos" && <RecursosView />}
          {abaValida === "processos" && <ProcessosView />}
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
      setErro(t("senha.nao_confere"));
      return;
    }
    setErro("");
    setEnviando(true);
    try {
      const r = await api.trocarSenha(atual, nova);
      // A troca invalidou o token anterior; sem guardar o novo, a próxima
      // requisição cairia para o login.
      if (r && r.access_token) setToken(r.access_token);
      await onPronto();
    } catch (ex) {
      setErro(ex.message);
      setEnviando(false);
    }
  }

  return (
    <div className="modal-bg" {...fecharSeForaLimpo(onFechar)}>
      <form className="modal" onClick={(e) => e.stopPropagation()} onSubmit={enviar}>
        <div className="modal-head">
          <div className="modal-title">{t("senha.trocar")}</div>
        </div>
        <div className="modal-body">
          {erro && <div className="login-err">{erro}</div>}
          <div className="field">
            <label className="label label-required">{t("senha.atual")}</label>
            <input type="password" value={atual} onChange={(e) => setAtual(e.target.value)} required autoFocus />
          </div>
          <div className="field">
            <label className="label label-required">{t("senha.nova")}</label>
            <input type="password" value={nova} onChange={(e) => setNova(e.target.value)} required minLength={6} />
            <div className="field-help">{t("senha.minimo")}</div>
          </div>
          <div className="field">
            <label className="label label-required">{t("senha.confirmar")}</label>
            <input type="password" value={confirmar} onChange={(e) => setConfirmar(e.target.value)} required />
          </div>
        </div>
        <div className="modal-foot">
          <button type="button" className="btn btn-secondary" onClick={onFechar}>
            {t("comum.cancelar")}
          </button>
          <button className="btn btn-primary" disabled={enviando}>
            {enviando ? t("comum.salvando") : t("senha.trocar")}
          </button>
        </div>
      </form>
    </div>
  );
}
