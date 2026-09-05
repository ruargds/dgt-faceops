import React, { useCallback, useEffect, useState } from "react";
import { api, formatData } from "../../api";
import { t } from "../../i18n";
import { Carregando, Erro, Vazio } from "../Comuns";
import { MARCA_PADRAO } from "../../marca";
import { useSessao } from "../../usePermissions";
import { IconAlerta, IconAtualizar, IconLixeira, IconMais, IconOk } from "../Icons";

/**
 * Avisos no Telegram — bot, destinos e regras.
 *
 * Três blocos, na ordem em que se configura, e o desenho é o de quem já
 * resolveu isso: Zabbix separa media type de action, Grafana separa
 * contact point de notification policy, Alertmanager separa receiver de
 * route. Aqui é **destino** (para onde) e **regra** (o que mandar para lá).
 *
 * Sem essa separação, cada destino novo exigiria duplicar todas as regras.
 */
const NIVEL_ROTULO = {
  critico: "Só quando parar",
  atencao: "Atenção e parada",
};

// Sugestão para o servidor cujos serviços o coletor ainda não listou.
// É sugestão, não validação: instalação pode ter serviço fora desta
// lista, e recusar o que não está aqui seria pior do que aceitar.
const SERVICOS_COMUNS = [
  "findface-multi-findface-multi-legacy-1",
  "findface-video-worker",
  "findface-video-manager",
  "findface-extraction-api",
  "findface-sf-api",
  "findface-ntls",
  "findface-upload",
  "findface-multi-postgresql-1",
  "findface-multi-mongodb-1",
  "findface-tarantool-server",
  "findface-counter",
  "findface-liveness-api",
];

// Segunda = 1, como no Zabbix (e ao contrário do getDay() do JavaScript,
// que começa no domingo em 0) — a numeração fica igual nos dois lados.
const DIAS_SEMANA = [
  { n: 1, curto: "seg" }, { n: 2, curto: "ter" }, { n: 3, curto: "qua" },
  { n: 4, curto: "qui" }, { n: 5, curto: "sex" }, { n: 6, curto: "sáb" },
  { n: 7, curto: "dom" },
];

/** 570 -> "09:30", para o <input type="time">. */
function minParaHora(min) {
  const m = Math.max(0, Math.min(1439, Number(min) || 0));
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** "09:30" -> 570. Campo limpo volta a zero, que é "o dia inteiro". */
function horaParaMin(texto) {
  const [h, m] = String(texto || "").split(":");
  if (h === undefined || m === undefined) return 0;
  return Math.max(0, Math.min(1439, Number(h) * 60 + Number(m)));
}

const ESPERAS = [
  { s: 0, rotulo: "na hora" },
  { s: 120, rotulo: "2 min" },
  { s: 300, rotulo: "5 min" },
  { s: 900, rotulo: "15 min" },
  { s: 1800, rotulo: "30 min" },
];

export default function NotificacoesView() {

  // Mesma assinatura que o backend monta, para a prévia não mentir sobre
  // o que vai chegar. O cliente vem da marca já carregada na sessão —
  // `projeto.cliente`, o mesmo campo do título do painel.
  const { marca } = useSessao();
  const cliente = ((marca || MARCA_PADRAO).cliente || "").trim();
  const assinatura = cliente ? `🤖 FaceOps · ${cliente}` : "🤖 FaceOps";
  const [conta, setConta] = useState(null);
  const [destinos, setDestinos] = useState([]);
  const [regras, setRegras] = useState([]);
  const [hosts, setHosts] = useState([]);
  const [tiposEvento, setTiposEvento] = useState([]);
  const [envios, setEnvios] = useState(null);
  const [chats, setChats] = useState(null);

  const [erro, setErro] = useState("");
  const [aviso, setAviso] = useState("");
  const [ocupado, setOcupado] = useState("");

  const [form, setForm] = useState({ bot_token: "", ativo: true });
  const [novoDestino, setNovoDestino] = useState({
    nome: "", tipo: "grupo", chat_id: "", observacao: "",
  });
  const [novaRegra, setNovaRegra] = useState(null);

  const carregar = useCallback(async () => {
    setErro("");
    try {
      const [c, d, r] = await Promise.all([
        api.notifConta(), api.notifDestinos(), api.notifRegras(),
      ]);
      setConta(c);
      setDestinos(d.destinos);
      setRegras(r.regras);
      setHosts(r.hosts);
      setTiposEvento(r.tipos_evento);
      setForm((f) => ({ ...f, ativo: c.ativo }));
    } catch (ex) {
      setErro(ex.message);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  async function acao(nome, fn, mensagem) {
    setOcupado(nome);
    setErro("");
    setAviso("");
    try {
      await fn();
      if (mensagem) setAviso(mensagem);
      await carregar();
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setOcupado("");
    }
  }

  async function testar(destinoId, nomeDestino) {
    setOcupado(`teste-${destinoId || "todos"}`);
    setErro("");
    setAviso("");
    try {
      const r = await api.testarNotif(destinoId);
      const falhas = (r.resultados || []).filter((x) => !x.ok);
      if (falhas.length === 0) {
        setAviso(
          destinoId
            ? `Mensagem de teste entregue em "${nomeDestino}". Confira o Telegram.`
            : "Mensagem de teste entregue em todos os destinos."
        );
      } else {
        setErro(
          falhas.map((f) => `${f.destino}: ${f.erro}`).join(" · ")
        );
      }
      setEnvios(null);
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setOcupado("");
    }
  }

  if (!conta && !erro) return <Carregando />;

  const semDestino = destinos.length === 0;
  const semRegra = regras.length === 0;
  const nomeHost = (id) => (id ? (hosts.find((h) => h.id === id) || {}).nome || `#${id}` : "todos os servidores");
  const nomeDestino = (id) =>
    id ? (destinos.find((d) => d.id === id) || {}).nome || `#${id}` : "todos os destinos ativos";

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("Avisos no Telegram")}</div>
          <div className="page-sub">
            {t("Um bot, quantos destinos precisar — grupo ou pessoa — e regras por tipo de evento")}
          </div>
        </div>
        <div className="page-actions">
          {!semDestino && conta.configurado && (
            <button
              className="btn btn-secondary"
              onClick={() => testar(null, null)}
              disabled={ocupado === "teste-todos"}
            >
              {ocupado === "teste-todos" ? t("enviando…") : t("Testar todos os destinos")}
            </button>
          )}
          <button className="btn btn-secondary" onClick={carregar}>
            <IconAtualizar size={15} /> {t("Recarregar")}
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {aviso && (
        <div
          className="card card-tight"
          style={{ background: "var(--green-bg)", borderColor: "var(--green-bd)", marginBottom: 14 }}
        >
          <span className="small" style={{ color: "var(--green-fg)" }}>
            <IconOk size={13} /> {aviso}
          </span>
        </div>
      )}

      {/* ── 1. O bot ────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>
          {t("1. O bot")}
        </div>
        <div className="small muted" style={{ marginBottom: 14 }}>
          {t("Crie no @BotFather e cole o token. Ele é validado no Telegram antes de salvar, guardado cifrado, e nunca é exibido de volta.")}
        </div>

        {conta.configurado && (
          <div className="stack-h small" style={{ gap: 10, marginBottom: 14, flexWrap: "wrap" }}>
            <span className={`pill ${conta.ativo ? "pill-ok" : "pill-idle"}`}>
              {conta.ativo ? t("envio habilitado") : t("envio desligado")}
            </span>
            <strong>{conta.bot_nome ? `@${conta.bot_nome}` : t("bot")}</strong>
            <span className="muted mono">{t("token")} {conta.token_fingerprint}</span>
            <span className="muted">
              {t("por")} {conta.atualizado_por} · {formatData(conta.atualizado_em)}
            </span>
          </div>
        )}

        <form
          onSubmit={(e) => {
            e.preventDefault();
            acao("conta", async () => {
              await api.salvarNotifConta(form);
              setForm((f) => ({ ...f, bot_token: "" }));
            }, t("Bot salvo."));
          }}
        >
          <div className="row row-2">
            <div className="field">
              <label className="label">
                {conta.configurado ? t("Trocar token do bot") : t("Token do bot")}
              </label>
              <input
                type="password"
                className="mono"
                autoComplete="new-password"
                placeholder={conta.configurado ? t("deixe vazio para manter") : "123456:ABC-DEF..."}
                value={form.bot_token}
                onChange={(e) => setForm({ ...form, bot_token: e.target.value })}
              />
            </div>
          </div>
          <div className="form-acao">
            <button className="btn btn-primary" disabled={ocupado === "conta"}>
              {ocupado === "conta" ? t("Salvando…") : t("Salvar bot")}
            </button>
          </div>

          {/* Habilitar é decisão própria, não um checkbox perdido. */}
          <div
            className="card card-tight"
            style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              gap: 16, flexWrap: "wrap",
              borderColor: form.ativo ? "var(--green-bd)" : "var(--border)",
              background: form.ativo ? "var(--green-bg)" : "var(--bg-2)",
            }}
          >
            <label className="check" style={{ margin: 0, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={form.ativo}
                onChange={(e) => {
                  const ativo = e.target.checked;
                  setForm((f) => ({ ...f, ativo }));
                  acao("conta", () => api.salvarNotifConta({ bot_token: "", ativo }),
                    ativo ? t("Envio de eventos habilitado.") : t("Envio de eventos desligado."));
                }}
              />
              <span>
                <strong style={{ color: form.ativo ? "var(--green-fg)" : "var(--text-2)" }}>
                  {form.ativo ? t("Envio de eventos habilitado") : t("Envio de eventos desligado")}
                </strong>
                <br />
                <span className="muted">
                  {form.ativo
                    ? t("Os eventos marcados nas regras abaixo são enviados aos destinos.")
                    : t("Nada sai daqui enquanto estiver desligado — destinos e regras ficam guardados.")}
                </span>
              </span>
            </label>
          </div>
        </form>
      </div>

      {/* ── 2. Destinos ─────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="stack-h" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
          <div>
            <div className="section-title" style={{ marginBottom: 4 }}>
              {t("2. Destinos")}
            </div>
            <div className="small muted">
              {t("Para onde os avisos vão. Pode ser grupo ou pessoa, e pode ser mais de um.")}
            </div>
          </div>
          {conta.configurado && (
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => acao("chats", async () => {
                const r = await api.notifChats();
                setChats(r.chats);
              })}
              disabled={ocupado === "chats"}
              title={t("Lista os chats que já falaram com o bot, para escolher em vez de digitar o id")}
            >
              {ocupado === "chats" ? t("procurando…") : t("descobrir chats")}
            </button>
          )}
        </div>

        {/* Descoberta: resolve o passo mais chato, que é achar o chat_id */}
        {chats && (
          <div className="card card-tight" style={{ marginTop: 12, background: "var(--bg-2)" }}>
            {chats.length === 0 ? (
              <div className="small muted">
                {t("Nenhum chat recente. Para aparecer aqui:")}
                <br />
                <strong>{t("grupo")}</strong> — {t("adicione o bot ao grupo e mande qualquer mensagem lá.")}
                <br />
                <strong>{t("pessoa")}</strong> — {t("ela precisa abrir conversa com o bot e mandar /start.")}
                <br />
                {t("Limite do Telegram: o bot precisa ser adicionado ao grupo, ou receber um /start. A lista só mostra quem falou nas últimas 24h.")}
              </div>
            ) : (
              <div className="stack-v" style={{ gap: 6 }}>
                <div className="small muted">{t("Clique para cadastrar como destino:")}</div>
                {chats.map((c) => (
                  <div key={c.chat_id} className="stack-h" style={{ justifyContent: "space-between", gap: 8 }}>
                    <span className="small">
                      <span className={`pill ${c.tipo === "grupo" ? "pill-info" : "pill-idle"}`}>
                        {c.tipo === "grupo" ? t("grupo") : t("pessoa")}
                      </span>{" "}
                      <strong>{c.nome}</strong> <span className="mono muted">{c.chat_id}</span>
                    </span>
                    {c.ja_cadastrado ? (
                      <span className="small muted">{t("já cadastrado")}</span>
                    ) : (
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => acao(`add-${c.chat_id}`, () => api.salvarNotifDestino({
                          nome: c.nome, tipo: c.tipo, chat_id: c.chat_id, ativo: true, observacao: "",
                        }), `"${c.nome}" cadastrado como destino.`)}
                      >
                        <IconMais size={12} /> {t("usar")}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {semDestino ? (
          <div style={{ marginTop: 12 }}>
            <Vazio titulo={t("Nenhum destino cadastrado")}>
              {t("Sem destino, nada é enviado. Use")} <strong>{t("descobrir chats")}</strong>{" "}
              {t("ou cadastre abaixo.")}
            </Vazio>
          </div>
        ) : (
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead>
                <tr>
                  <th>{t("Destino")}</th>
                  <th>{t("Tipo")}</th>
                  <th>{t("Chat")}</th>
                  <th>{t("Ativo")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {destinos.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <strong>{d.nome}</strong>
                      {d.observacao && <div className="small muted">{d.observacao}</div>}
                    </td>
                    <td>
                      <span className={`pill ${d.tipo === "grupo" ? "pill-info" : "pill-idle"}`}>
                        {d.tipo === "grupo" ? t("grupo") : t("pessoa")}
                      </span>
                    </td>
                    <td className="mono small">{d.chat_id}</td>
                    <td>
                      <input
                        type="checkbox"
                        checked={d.ativo}
                        onChange={(e) => acao(`d-${d.id}`, () => api.salvarNotifDestino({
                          nome: d.nome, tipo: d.tipo, chat_id: d.chat_id,
                          observacao: d.observacao, ativo: e.target.checked,
                        }))}
                      />
                    </td>
                    <td>
                      <div className="stack-h" style={{ gap: 4 }}>
                        {/* Editar carrega o destino no formulário abaixo,
                            em vez de abrir outro lugar para a mesma
                            coisa. Inclui trocar o id do chat: grupo que
                            vira supergrupo MUDA de id, e sem isto o
                            caminho era apagar e cadastrar de novo —
                            levando junto as regras que apontavam para
                            ele. */}
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => {
                            setNovoDestino({
                              id: d.id, nome: d.nome, tipo: d.tipo,
                              chat_id: d.chat_id, observacao: d.observacao || "",
                            });
                            setAviso("");
                          }}
                          title={t("Editar este destino")}
                        >
                          {t("editar")}
                        </button>
                        {/* O botão que prova que funciona. */}
                        <button
                          className="btn btn-secondary btn-sm"
                          disabled={!conta.configurado || ocupado === `teste-${d.id}`}
                          onClick={() => testar(d.id, d.nome)}
                          title={t("Manda uma mensagem agora para este destino")}
                        >
                          {ocupado === `teste-${d.id}` ? t("enviando…") : t("testar")}
                        </button>
                        <button
                          className="btn btn-danger btn-sm"
                          onClick={() => {
                            if (!window.confirm(`Remover o destino "${d.nome}"? As regras que apontam só para ele saem junto.`)) return;
                            acao(`rm-${d.id}`, () => api.removerNotifDestino(d.id), t("Destino removido."));
                          }}
                        >
                          <IconLixeira size={13} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <form
          style={{ marginTop: 14 }}
          onSubmit={(e) => {
            e.preventDefault();
            const editando = Boolean(novoDestino.id);
            acao("novo-destino", async () => {
              await api.salvarNotifDestino({
                ...novoDestino,
                // Na edição preserva o ligado/desligado que está na
                // tabela; na criação entra ativo.
                ativo: editando
                  ? (destinos.find((x) => x.id === novoDestino.id) || {}).ativo !== false
                  : true,
              });
              setNovoDestino({ nome: "", tipo: "grupo", chat_id: "", observacao: "" });
            }, editando ? t("Destino atualizado.") : t("Destino cadastrado."));
          }}
        >
          <div className="row row-3 form-linha">
            <div className="field">
              <label className="label label-required">{t("Nome")}</label>
              <input
                placeholder="Plantão NOC"
                value={novoDestino.nome}
                onChange={(e) => setNovoDestino({ ...novoDestino, nome: e.target.value })}
                required
              />
            </div>
            <div className="field">
              <label className="label">{t("Tipo")}</label>
              <select
                value={novoDestino.tipo}
                onChange={(e) => setNovoDestino({ ...novoDestino, tipo: e.target.value })}
              >
                <option value="grupo">{t("Grupo")}</option>
                <option value="individual">{t("Pessoa (conversa direta)")}</option>
              </select>
            </div>
            <div className="field">
              <label className="label label-required">{t("Id do chat")}</label>
              <input
                className="mono"
                placeholder={novoDestino.tipo === "grupo" ? "-1001234567890" : "123456789"}
                value={novoDestino.chat_id}
                onChange={(e) => setNovoDestino({ ...novoDestino, chat_id: e.target.value })}
                required
              />
            </div>
          </div>

          {/* Ajuda da linha inteira: dentro de um campo só, ela deixava
              aquela célula mais alta que as vizinhas e torcia a linha. */}
          <div className="form-ajuda">
            {novoDestino.tipo === "grupo"
              ? t("Grupo tem id negativo, começando por -100. O bot precisa ter sido adicionado ao grupo.")
              : t("Id numérico da pessoa. Ela precisa ter aberto conversa com o bot e mandado /start.")}
          </div>

          <div className="form-acao">
            {novoDestino.id && (
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setNovoDestino({ nome: "", tipo: "grupo", chat_id: "", observacao: "" })}
              >
                {t("cancelar")}
              </button>
            )}
            <button className="btn btn-primary" disabled={ocupado === "novo-destino"}>
              {novoDestino.id ? (
                t("Salvar destino")
              ) : (
                <>
                  <IconMais size={14} /> {t("Adicionar destino")}
                </>
              )}
            </button>
          </div>
        </form>
      </div>

      {/* ── 3. Regras ───────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="stack-h" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
          <div>
            <div className="section-title" style={{ marginBottom: 4 }}>
              {t("3. Regras — o que mandar, e para quem")}
            </div>
            <div className="small muted">
              {t("Sem regra ligada, nada é enviado. Regras diferentes valem ao mesmo tempo.")}
            </div>
          </div>
          <button
            className="btn btn-secondary btn-sm"
            disabled={semDestino}
            onClick={() => setNovaRegra({
              destino_id: null, host_id: null, servico: "",
              tipos: ["servico_parado", "host_sem_contato", "retorno"],
              nivel_minimo: "critico", atraso_s: 0, ativo: true,
              dias_semana: [], hora_inicio_min: 0, hora_fim_min: 0,
            })}
          >
            <IconMais size={13} /> {t("nova regra")}
          </button>
        </div>

        {semRegra && !novaRegra ? (
          <div style={{ marginTop: 12 }}>
            <Vazio titulo={t("Nenhuma regra")}>
              {t("Nada é enviado até existir uma regra. Comece por uma geral: todos os destinos e servidores.")}
            </Vazio>
          </div>
        ) : (
          <div className="stack-v" style={{ gap: 10, marginTop: 12 }}>
            {[...regras, ...(novaRegra ? [novaRegra] : [])].map((r, i) => (
              <FormRegra
                key={r.id || `nova-${i}`}
                regra={r}
                hosts={hosts}
                destinos={destinos}
                tiposEvento={tiposEvento}
                nomeHost={nomeHost}
                nomeDestino={nomeDestino}
                ocupado={ocupado}
                onSalvar={(dados) => acao(`r-${r.id || "nova"}`, async () => {
                  await api.salvarNotifRegra(dados);
                  setNovaRegra(null);
                }, t("Regra salva."))}
                onRemover={r.id ? () => {
                  if (!window.confirm("Remover esta regra?")) return;
                  acao(`rr-${r.id}`, () => api.removerNotifRegra(r.id), t("Regra removida."));
                } : () => setNovaRegra(null)}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Como chega ──────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="section-title" style={{ marginBottom: 4 }}>{t("Como a mensagem chega")}</div>
        <div className="small muted" style={{ marginBottom: 10 }}>
          {t("Mesmo formato do Zabbix, para não haver dois padrões no mesmo grupo. A primeira linha diz de onde veio o aviso.")}
        </div>
        <div className="small muted" style={{ marginBottom: 10 }}>
          {t("O nome do cliente vem de Configurações → Identidade do projeto — o mesmo campo que já nomeia o painel.")}
          {" "}
          {cliente
            ? <><strong>{t("Agora está como")}:</strong> {assinatura}</>
            : <em>{t("Ainda não preenchido — a assinatura sai só como FaceOps.")}</em>}
        </div>
        <div className="row row-2">
          {[
            `${assinatura}

🔴 - vm-appserver (Aplicação) - 🔴

⚠️ - Problema: o serviço findface-video-worker parou de funcionar

💬 - Significa: É ele que processa o vídeo das câmeras. Enquanto estiver fora, este servidor não reconhece ninguém.

🔎 - Provável: reiniciou 7x nos últimos 30 min

🛠 - Fazer: Em Serviços, abra o log deste container.

⏳ - Iniciado em: 02/09 14:32:07 (há 6m 20s)

⚡ - Gravidade: Crítico`,
            `${assinatura}

✅✅ - vm-appserver (Aplicação) - ✅✅

✅ - Resolvido: findface-video-worker voltou a funcionar

⏱ - Duração: 6m 20s

🕐 - Horário: 02/09 14:38:27`,
            `${assinatura}

⛔ - vm-dbserver (Banco de dados) - ⛔

⚠️ - Problema: o servidor não respondeu ao monitoramento

💬 - Significa: Nada pode ser verificado nesta máquina agora — inclusive o Face Detect, que pode estar rodando normal. Falha de rede dá este mesmo aviso.

🔎 - Provável: rede fora, VM desligada ou parada

🛠 - Fazer: Confira se a VM está ligada e a rede de pé antes de investigar o Face Detect.

⏳ - Iniciado em: 02/09 03:10:44 (há 12m 8s)

⚡ - Gravidade: Crítico`,
            `${assinatura}

🟡 - vm-ftpserver (FTP / arquivos) - 🟡

⚠️ - Problema: CPU sobrecarregada — 1.16 processo por núcleo (o normal é abaixo de 1,00)

💬 - Significa: Há processo esperando a vez de usar o processador. Nada parou, mas tudo responde mais devagar, inclusive o reconhecimento.

🛠 - Fazer: Em Recursos, veja quais containers estão consumindo mais CPU.

⚡ - Gravidade: Atenção`,
          ].map((exemplo, i) => (
            <pre
              key={i}
              className="mono small"
              style={{
                background: "var(--bg-2)", padding: 12, borderRadius: "var(--radius)",
                margin: 0, whiteSpace: "pre-wrap",
              }}
            >
              {exemplo}
            </pre>
          ))}
        </div>
      </div>

      {/* ── Últimos envios ─────────────────────────────────────────── */}
      <div className="card">
        <div className="stack-h" style={{ justifyContent: "space-between" }}>
          <div>
            <div className="section-title" style={{ marginBottom: 4 }}>{t("Últimos envios")}</div>
            <div className="small muted">
              {t("É a resposta para 'não recebi'. Guardado por 14 dias e apagado pela faxina.")}
            </div>
          </div>
          <button
            className="btn btn-secondary btn-sm"
            onClick={() => acao("envios", async () => {
              const r = await api.notifEnvios(20);
              setEnvios(r.envios);
            })}
          >
            {t("ver envios")}
          </button>
        </div>

        {envios && (
          envios.length === 0 ? (
            <div className="small muted" style={{ marginTop: 12 }}>{t("Nada enviado ainda.")}</div>
          ) : (
            <div className="table-wrap" style={{ marginTop: 12 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("Quando")}</th>
                    <th>{t("Destino")}</th>
                    <th>{t("Mensagem")}</th>
                    <th>{t("Situação")}</th>
                  </tr>
                </thead>
                <tbody>
                  {envios.map((e) => (
                    <tr key={e.id}>
                      <td className="small">{formatData(e.ts)}</td>
                      <td className="small">{e.destino || "—"}</td>
                      <td className="small mono" style={{ whiteSpace: "pre-wrap" }}>{e.texto}</td>
                      <td>
                        {e.status === "enviado" ? (
                          <span className="pill pill-ok">{t("enviado")}</span>
                        ) : (
                          <span className="pill pill-err" title={e.erro}>
                            <IconAlerta size={12} /> {t("falhou")}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        )}
      </div>
    </>
  );
}

/**
 * Uma regra. Editada no lugar — abrir modal para trocar uma caixa de
 * seleção é passo a mais sem ganho.
 */
function FormRegra({
  regra, hosts, destinos, tiposEvento, nomeHost, nomeDestino, ocupado,
  onSalvar, onRemover,
}) {
  const [r, setR] = useState(regra);
  const nova = !regra.id;
  const host = hosts.find((h) => h.id === r.host_id);

  const alternarTipo = (chave) =>
    setR((atual) => ({
      ...atual,
      tipos: atual.tipos.includes(chave)
        ? atual.tipos.filter((x) => x !== chave)
        : [...atual.tipos, chave],
    }));

  const mudou = JSON.stringify(r) !== JSON.stringify(regra);

  return (
    <div
      className="card card-tight"
      style={{
        borderLeftWidth: 4,
        borderLeftColor: r.ativo ? "var(--blue)" : "var(--border-2)",
        opacity: r.ativo ? 1 : 0.75,
      }}
    >
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 10, gap: 10, flexWrap: "wrap" }}>
        <div className="small">
          <strong>
            {nova ? "Nova regra" : `${nomeDestino(r.destino_id)} ← ${nomeHost(r.host_id)}`}
          </strong>
          {!nova && r.servico && <span className="mono"> · {r.servico}</span>}
        </div>
        <div className="stack-h" style={{ gap: 6 }}>
          <label className="check" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={r.ativo}
              onChange={(e) => setR({ ...r, ativo: e.target.checked })}
            />
            <span>{t("ativa")}</span>
          </label>
          {/* O salvar mora no RODAPÉ do formulário, não aqui: quem
              termina de editar está com o olho na última linha, e um
              botão que aparece no topo só quando algo muda é um botão
              que ninguém encontra. */}
          <button className="btn btn-danger btn-sm" onClick={onRemover}>
            <IconLixeira size={13} />
          </button>
        </div>
      </div>

      <div className="row row-4 form-linha">
        <div className="field">
          <label className="label">{t("Enviar para")}</label>
          <select
            value={r.destino_id ?? ""}
            onChange={(e) => setR({ ...r, destino_id: e.target.value === "" ? null : Number(e.target.value) })}
          >
            <option value="">{t("todos os destinos ativos")}</option>
            {destinos.map((d) => (
              <option key={d.id} value={d.id}>{d.nome}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="label">{t("Servidor")}</label>
          <select
            value={r.host_id ?? ""}
            onChange={(e) => setR({
              ...r,
              host_id: e.target.value === "" ? null : Number(e.target.value),
              servico: "",
            })}
          >
            <option value="">{t("todos")}</option>
            {hosts.map((h) => (
              <option key={h.id} value={h.id}>{h.nome}</option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="label">{t("Serviço")}</label>
          {/* Três estados, e cada um precisa dizer a verdade. Antes havia
              um só: sem serviço conhecido o campo ficava morto dizendo
              "escolha um servidor primeiro" — mesmo com servidor
              escolhido. Quem escolhia ficava sem saída, sem entender por
              quê. Agora, servidor cujos serviços o coletor ainda não
              listou aceita o nome digitado, com a lista como sugestão. */}
          {!host || (host.servicos || []).length > 0 ? (
            <select
              value={r.servico}
              onChange={(e) => setR({ ...r, servico: e.target.value })}
              disabled={!host}
            >
              <option value="">{t("todos")}</option>
              {(host ? host.servicos : []).map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          ) : (
            <>
              <input
                className="mono"
                list={`serv-${r.id || "novo"}`}
                placeholder={t("todos")}
                value={r.servico}
                onChange={(e) => setR({ ...r, servico: e.target.value.trim() })}
              />
              <datalist id={`serv-${r.id || "novo"}`}>
                {SERVICOS_COMUNS.map((s) => (
                  <option key={s} value={s} />
                ))}
              </datalist>
            </>
          )}
        </div>
        <div className="field">
          <label className="label">{t("Gravidade mínima")}</label>
          <select
            value={r.nivel_minimo}
            onChange={(e) => setR({ ...r, nivel_minimo: e.target.value })}
          >
            {Object.entries(NIVEL_ROTULO).map(([v, rot]) => (
              <option key={v} value={v}>{t(rot)}</option>
            ))}
          </select>
        </div>
      </div>

      {!host && (
        <div className="form-ajuda">
          {t("Sem servidor escolhido a regra vale para todos — e aí não faz sentido restringir a um serviço.")}
        </div>
      )}
      {host && (host.servicos || []).length === 0 && (
        <div className="form-ajuda">
          {t("O coletor ainda não listou os serviços deste servidor. Escreva o nome, ou deixe vazio para todos.")}
        </div>
      )}

      <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
        <div className="label" style={{ marginBottom: 8 }}>{t("Tipos de evento")}</div>
        <div style={{ display: "grid", gap: 6, gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
          {tiposEvento.map((tp) => (
            <label key={tp.chave} className="check" style={{ margin: 0 }}>
              <input
                type="checkbox"
                checked={r.tipos.includes(tp.chave)}
                onChange={() => alternarTipo(tp.chave)}
              />
              <span>
                {tp.icone} <strong>{tp.rotulo}</strong>
                <br />
                <span className="muted" style={{ fontSize: 12 }}>{tp.ajuda}</span>
              </span>
            </label>
          ))}
        </div>
        {r.tipos.length === 0 && (
          <div className="small" style={{ color: "var(--amber-fg)", marginTop: 6 }}>
            {t("Nenhum tipo marcado — esta regra não vai mandar nada.")}
          </div>
        )}

        <div className="row row-4 form-linha" style={{ marginTop: 12 }}>
          <div className="field">
            <label className="label">{t("Avisar depois de")}</label>
            <select
              value={r.atraso_s}
              onChange={(e) => setR({ ...r, atraso_s: Number(e.target.value) })}
            >
              {ESPERAS.map((e) => (
                <option key={e.s} value={e.s}>{t(e.rotulo)}</option>
              ))}
            </select>
          </div>
        </div>
        <div className="form-ajuda">
          {t("Só avisa se o problema persistir por esse tempo — evita acordar alguém por uma piscada. O retorno ao normal nunca espera.")}
        </div>

        {/* ── Quando esta regra vale ─────────────────────────────────
            O "time period" do Zabbix. Fica depois da espera porque é a
            última pergunta que se faz ao montar uma regra: já se sabe o
            que mandar e para quem, falta quando. */}
        <div className="section-title" style={{ marginTop: 18, marginBottom: 8 }}>
          {t("Quando esta regra vale")}
        </div>

        <div className="stack-h" style={{ gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
          {DIAS_SEMANA.map((d) => {
            const marcado = (r.dias_semana || []).includes(d.n);
            return (
              <button
                key={d.n}
                type="button"
                className={`btn btn-sm ${marcado ? "btn-primary" : "btn-secondary"}`}
                style={{ minWidth: 46 }}
                onClick={() => {
                  const atual = r.dias_semana || [];
                  setR({
                    ...r,
                    dias_semana: marcado
                      ? atual.filter((x) => x !== d.n)
                      : [...atual, d.n].sort((a, b) => a - b),
                  });
                }}
              >
                {t(d.curto)}
              </button>
            );
          })}
        </div>

        <div className="row row-4 form-linha">
          <div className="field">
            <label className="label">{t("Das")}</label>
            <input
              type="time"
              value={minParaHora(r.hora_inicio_min)}
              onChange={(e) =>
                setR({ ...r, hora_inicio_min: horaParaMin(e.target.value) })
              }
            />
          </div>
          <div className="field">
            <label className="label">{t("Até")}</label>
            <input
              type="time"
              value={minParaHora(r.hora_fim_min)}
              onChange={(e) =>
                setR({ ...r, hora_fim_min: horaParaMin(e.target.value) })
              }
            />
          </div>
        </div>
        <div className="form-ajuda">
          {/* A frase acompanha o que está configurado. Dizer "fora deste
              horário a regra não manda nada" quando início e fim são
              iguais seria mentir: aí ela vale o dia inteiro. */}
          {r.hora_inicio_min === r.hora_fim_min
            ? ((r.dias_semana || []).length === 0
                ? t("Vale sempre: todos os dias, o dia inteiro.")
                : t("Nos dias marcados, o dia inteiro."))
            : r.hora_fim_min < r.hora_inicio_min
              ? t("A janela cruza a meia-noite — é o turno da madrugada. O horário final entra por completo (até 23:59 cobre o minuto inteiro).")
              : t("Fora deste dia e horário a regra não manda nada, nem o retorno. O horário final entra por completo.")}
        </div>

        {/* ── Rodapé: o estado, e o que fazer com ele ────────────────
            Sempre visível. Botão que só aparece depois de mexer em algo
            deixa a pessoa sem saber se precisava salvar — e some da vista
            justamente quem rolou até o fim do formulário para editar. */}
        <div
          className="stack-h"
          style={{
            justifyContent: "space-between", alignItems: "center", gap: 10,
            flexWrap: "wrap", marginTop: 16, paddingTop: 12,
            borderTop: "1px solid var(--border)",
          }}
        >
          <span className="small" style={{ color: mudou ? "var(--amber-fg)" : "var(--text-3)" }}>
            {mudou
              ? t("Alterações ainda não salvas.")
              : (nova ? t("Preencha e salve para criar a regra.") : t("Tudo salvo."))}
          </span>
          <button
            className={`btn btn-sm ${mudou ? "btn-primary" : "btn-secondary"}`}
            disabled={!mudou || ocupado.startsWith("r-")}
            onClick={() => onSalvar(r)}
          >
            {ocupado.startsWith("r-")
              ? t("Salvando…")
              : (nova ? t("Criar regra") : t("Salvar alterações"))}
          </button>
        </div>
      </div>
    </div>
  );
}
