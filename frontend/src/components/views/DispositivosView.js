import React, { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatData } from "../../api";
import { t } from "../../i18n";
import { Carregando, Erro, Medidor, SeletorHost, Vazio, useHosts } from "../Comuns";
import { BarraMetrica } from "../Graficos";
import { IconAtualizar, IconDownload, IconAlerta, IconOk } from "../Icons";

const PERIODOS = [
  { id: "hora", rotulo: "1 hora" },
  { id: "dia", rotulo: "24 horas" },
  { id: "semana", rotulo: "7 dias" },
  { id: "mes", rotulo: "30 dias" },
];

/**
 * Câmeras cadastradas no FindFace: quantas, quando falaram, quanto geram.
 *
 * Consulta pesada e sob demanda — lê o banco do FindFace e agrega. Nunca
 * fica atualizando sozinha: contar evento a cada minuto seria o peso que
 * o painel promete não criar.
 */
/**
 * Licenciamento do FindFace.
 *
 * É a mesma tela de licenças da plataforma da NtechLab, trazida para cá:
 * identificação e validade no topo, e a tabela de recursos com o que está
 * **em uso** e o que está **liberado**. Existe porque "cabem quantas
 * câmeras ainda?" e "estamos estourando algum limite?" eram perguntas que
 * só tinham resposta entrando na interface do fabricante.
 *
 * O número de uso muda o tempo todo, então há botão de reler — a leitura é
 * barata (um GET, sem SSH), mas não fica se atualizando sozinha: quem
 * quiser o número de agora pede o número de agora.
 *
 * Recurso estourado (usado > liberado, como o `Objects TNT API` a
 * 2.400.054 de 2.400.000 na instalação real) sobe para o topo e aparece em
 * vermelho: é o que trava operação sem avisar ninguém.
 */
function Licenciamento({ dados, erro, lendo, onAtualizar }) {
  const [verBruto, setVerBruto] = useState(false);

  if (erro) {
    return (
      <div className="card card-tight">
        <div className="stack-h" style={{ justifyContent: "space-between" }}>
          <div className="section-title" style={{ marginBottom: 0 }}>{t("Licenciamento")}</div>
          <button className="btn btn-ghost btn-sm" onClick={onAtualizar} disabled={lendo}>
            <IconAtualizar size={14} /> {lendo ? "Lendo…" : "Tentar de novo"}
          </button>
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>{erro}</div>
      </div>
    );
  }
  if (!dados) {
    return lendo ? <Carregando texto={t("Lendo o licenciamento do FindFace…")} /> : null;
  }

  const cab = dados.cabecalho || {};
  // Estourado primeiro; depois o mais ocupado. Quem abre a tela precisa ver
  // o problema sem procurar.
  const itens = [...(dados.itens || [])].sort((a, b) => {
    if (Boolean(b.estourado) !== Boolean(a.estourado)) return b.estourado ? 1 : -1;
    const oa = a.limite > 0 && a.usado != null ? a.usado / a.limite : -1;
    const ob = b.limite > 0 && b.usado != null ? b.usado / b.limite : -1;
    return ob - oa;
  });

  const numero = (v) =>
    v === null || v === undefined ? "—" : Number(v).toLocaleString("pt-BR");

  return (
    <div className="card">
      <div className="stack-h" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <div className="section-title" style={{ marginBottom: 0 }}>
          Licenciamento
          {dados.estourados > 0 && (
            <span className="pill pill-err" style={{ marginLeft: 8 }}>
              <IconAlerta size={11} /> {dados.estourados} limite(s) estourado(s)
            </span>
          )}
          {dados.estourados === 0 && cab.valido && (
            <span className="pill pill-ok" style={{ marginLeft: 8 }}>
              <IconOk size={11} /> {t("válida")}</span>
          )}
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onAtualizar} disabled={lendo}>
          <IconAtualizar size={14} /> {lendo ? "Lendo…" : "Atualizar uso"}
        </button>
      </div>

      {/* A explicação de cada número fica no `title`, não na tela: três
          linhas de legenda por cartão viram parede de texto, e quem opera
          já sabe o que é "validade". Quem não sabe para o mouse em cima. */}
      <div className="grid-stats" style={{ marginBottom: 12 }}>
        <div
          className="card card-tight stat"
          title={`Identificador da licença no FindFace${cab.tipo ? ` · tipo ${cab.tipo}` : ""}`}
        >
          <span className="stat-label">{t("Identificação")}</span>
          <div className="mono small" style={{ wordBreak: "break-all" }}>
            {cab.id || "—"}
          </div>
        </div>
        <div
          className="card card-tight stat"
          title={
            cab.valido === false
              ? "Esta licença está inválida"
              : cab.dias_para_expirar != null
              ? `Faltam ${cab.dias_para_expirar} dia(s) para expirar`
              : "Data de expiração da licença"
          }
        >
          <span className="stat-label">{t("Validade")}</span>
          <div
            className="stat-value"
            style={cab.expira_em_breve ? { color: "var(--amber)" } : undefined}
          >
            {cab.validade || "—"}
          </div>
          {cab.expira_em_breve && (
            <span className="stat-sub" style={{ color: "var(--amber)" }}>
              expira em {cab.dias_para_expirar} dia(s)
            </span>
          )}
        </div>
        <div
          className="card card-tight stat"
          title="Arquivo .lic no servidor do FindFace"
        >
          <span className="stat-label">{t("Arquivo")}</span>
          <div className="mono small" style={{ wordBreak: "break-all" }}>
            {cab.arquivo || "—"}
          </div>
        </div>
        <div
          className="card card-tight stat"
          title={
            "Câmeras cadastradas no FindFace, contadas no momento desta leitura. " +
            "Detector externo é sistema de fora empurrando evento pela API: " +
            "aparece na mesma lista e consome licença, mas não é câmera."
          }
        >
          <span className="stat-label">{t("Câmeras cadastradas")}</span>
          <div className="stat-value">
            {dados.cameras_cadastradas === null || dados.cameras_cadastradas === undefined
              ? "—"
              : dados.cameras_cadastradas}
          </div>
          {dados.detectores_externos != null && (
            <span className="stat-sub">
              {dados.cameras_reais != null ? `${dados.cameras_reais} câmera(s) · ` : ""}
              {dados.detectores_externos} detector(es) externo(s)
            </span>
          )}
        </div>
      </div>

      {itens.length === 0 ? (
        <div className="small muted">
          A licença respondeu, mas nenhum limite reconhecível veio no corpo.
          Veja o conteúdo bruto abaixo.
        </div>
      ) : (
        <div className="table-wrap">
          <table className="tabela-densa">
            <thead>
              <tr>
                <th>{t("Recurso")}</th>
                <th className="right">{t("Em uso")}</th>
                <th className="right">{t("Liberado")}</th>
                <th className="right">{t("Livre")}</th>
                <th style={{ width: 150 }}>{t("Ocupação")}</th>
              </tr>
            </thead>
            <tbody>
              {itens.map((i, idx) => {
                const pct =
                  i.limite > 0 && i.usado !== null && i.usado !== undefined
                    ? (i.usado / i.limite) * 100
                    : null;
                return (
                  <tr key={`${i.recurso}-${idx}`}>
                    <td>
                      {i.recurso}
                      {i.estourado && (
                        <span className="pill pill-err" style={{ marginLeft: 8 }}>
                          estourado
                        </span>
                      )}
                    </td>
                    <td
                      className="right mono"
                      style={i.estourado ? { color: "var(--red)", fontWeight: 600 } : undefined}
                    >
                      {numero(i.usado)}
                    </td>
                    <td className="right mono">
                      {i.ilimitado ? "ilimitado" : numero(i.limite)}
                    </td>
                    <td
                      className="right mono"
                      style={i.restante < 0 ? { color: "var(--red)" } : undefined}
                    >
                      {i.ilimitado ? "—" : numero(i.restante)}
                    </td>
                    <td>
                      {pct === null ? (
                        <span className="small muted">—</span>
                      ) : (
                        <div className="stack-h" style={{ gap: 8 }}>
                          <Medidor pct={Math.min(pct, 100)} />
                          <span className="mono small">{pct.toFixed(0)}%</span>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {dados.funcionalidades && dados.funcionalidades.length > 0 && (
        <div style={{ marginTop: 12 }}>
          <div className="section-title" style={{ marginBottom: 6 }}>
            {t("Módulos licenciados")}
          </div>
          <div className="stack-h" style={{ flexWrap: "wrap", gap: 6 }}>
            {dados.funcionalidades.map((f) => (
              <span
                key={f.chave}
                className={`pill ${f.ligado ? "pill-ok" : "pill-idle"}`}
                title={
                  f.ligado
                    ? "Habilitado nesta licença"
                    : "Existe no produto, mas não está habilitado nesta licença"
                }
              >
                {f.nome}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="stack-h" style={{ marginTop: 12 }}>
        <span
          className="small muted"
          style={{ flex: 1 }}
          title={
            dados.via === "ssh"
              ? "Lido de dentro do servidor, por SSH — o serviço de licença atende em localhost sem pedir login"
              : "Lido pela API HTTP do FindFace, com a credencial cadastrada"
          }
        >
          {t("Lido de")} <span className="mono">{dados.caminho}</span>
          {dados.via === "ssh" ? " (pelo servidor)" : " (pela API)"}
        </span>
        <button className="btn btn-ghost btn-sm" onClick={() => setVerBruto((v) => !v)}>
          {verBruto ? "Esconder resposta bruta" : "Ver resposta bruta"}
        </button>
      </div>

      {verBruto && (
        <pre
          className="mono small"
          style={{ marginTop: 8, maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap" }}
        >
          {JSON.stringify(dados.bruto, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function DispositivosView() {
  const { hosts, hostId, setHostId, carregando: carregandoHosts } = useHosts();
  const [dados, setDados] = useState(null);
  const [periodo, setPeriodo] = useState("dia");
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(false);
  const [filtro, setFiltro] = useState("");
  const [licenca, setLicenca] = useState(null);
  const [erroLicenca, setErroLicenca] = useState("");
  const [lendoLicenca, setLendoLicenca] = useState(false);

  const consultar = useCallback(async () => {
    if (!hostId) return;
    setCarregando(true);
    setErro("");
    try {
      setDados(await api.dispositivos(hostId, periodo));
    } catch (ex) {
      setErro(ex.message);
      setDados(null);
    } finally {
      setCarregando(false);
    }
  }, [hostId, periodo]);

  useEffect(() => {
    setDados(null);
  }, [hostId]);

  // Licença é leitura barata (um GET, sem SSH e sem varrer evento), então
  // carrega sozinha ao trocar de servidor — diferente da contagem de
  // eventos, que continua sob demanda. Assim a tela responde algo útil
  // antes de alguém clicar em Consultar.
  const lerLicenca = useCallback(async () => {
    if (!hostId) return;
    setLendoLicenca(true);
    setErroLicenca("");
    try {
      setLicenca(await api.licencaFindFace(hostId));
    } catch (ex) {
      setLicenca(null);
      setErroLicenca(ex.message);
    } finally {
      setLendoLicenca(false);
    }
  }, [hostId]);

  useEffect(() => {
    setLicenca(null);
    lerLicenca();
  }, [lerLicenca]);

  if (carregandoHosts) return <Carregando />;
  if (!hosts.length) return <Vazio titulo={t("Cadastre um servidor primeiro")} />;

  const cameras = dados
    ? dados.cameras.filter((c) =>
        !filtro || c.nome.toLowerCase().includes(filtro.toLowerCase())
      )
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">{t("tela.cameras")}</div>
          <div className="page-sub">
            {t("tela.cameras.sub")}
          </div>
        </div>
        <div className="page-actions">
          <SeletorHost hosts={hosts} hostId={hostId} onMudar={setHostId} />
          <select value={periodo} onChange={(e) => setPeriodo(e.target.value)} style={{ width: "auto" }}>
            {PERIODOS.map((p) => (
              <option key={p.id} value={p.id}>{p.rotulo}</option>
            ))}
          </select>
          <button className="btn btn-primary" onClick={consultar} disabled={carregando || !hostId}>
            <IconAtualizar size={15} /> {carregando ? "Consultando…" : "Consultar"}
          </button>
          {dados && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() =>
                api
                  .baixar(api.urlExportarDispositivos(hostId, periodo), `cameras-${periodo}.csv`)
                  .catch((e) => setErro(e.message))
              }
              title={t("Baixar em CSV")}
            >
              <IconDownload size={15} /> {t("Exportar")}</button>
          )}
        </div>
      </div>

      <Erro mensagem={erro} onTentar={consultar} />

      <Licenciamento
        dados={licenca}
        erro={erroLicenca}
        lendo={lendoLicenca}
        onAtualizar={lerLicenca}
      />

      {!dados && !carregando && (
        <Vazio titulo={t("Clique em Consultar")}>
          Lê o banco do FindFace e conta os eventos por câmera. Pode levar alguns
          segundos em base grande — por isso é sob demanda, não automático.
        </Vazio>
      )}

      {carregando && !dados && <Carregando texto={t("Contando eventos no banco do FindFace…")} />}

      {dados && (
        <div className="stack-v">
          <div className="grid-stats">
            <div className="card card-tight stat">
              <span className="stat-label">{t("Câmeras cadastradas")}</span>
              <div className="stat-value">{dados.total_cameras}</div>
              <span className="stat-sub">{dados.esquema.banco}</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">Comunicando ({dados.periodo_rotulo})</span>
              <div className="stat-value" style={{ color: "var(--green)" }}>
                {dados.cameras_com_evento}
              </div>
              <span className="stat-sub">{t("geraram ao menos um evento")}</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">{t("Sem eventos")}</span>
              <div
                className="stat-value"
                style={{ color: dados.cameras_mudas > 0 ? "var(--amber)" : "var(--text-3)" }}
              >
                {dados.cameras_mudas}
              </div>
              <span className="stat-sub">{t("nada no período — pode estar offline")}</span>
            </div>
            <div className="card card-tight stat">
              <span className="stat-label">{t("Total de eventos")}</span>
              <div className="stat-value">{dados.total_eventos.toLocaleString("pt-BR")}</div>
              <span className="stat-sub">{t("no período")}</span>
            </div>
          </div>

          <div className="stack-h" style={{ justifyContent: "space-between" }}>
            <input
              value={filtro}
              onChange={(e) => setFiltro(e.target.value)}
              placeholder={t("filtrar câmera pelo nome…")}
              style={{ maxWidth: 280 }}
            />
            {dados.contagem_por_camera === false && (
              <span className="small muted" title="Contar evento por camera custa uma requisicao por camera e por tipo; com centenas de dispositivos isso seria mais de mil chamadas na API de producao">
                Com {dados.total_cameras} dispositivos, a contagem foi feita no
                total — não por dispositivo.
              </span>
            )}
            {dados.estimativa && (
              <span className="small muted">
                Volume por câmera é estimativa (rateio pela participação nos eventos).
              </span>
            )}
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("Câmera")}</th>
                  <th>{t("Situação")}</th>
                  <th className="right">Eventos ({dados.periodo_rotulo})</th>
                  <th style={{ width: 140 }}>{t("Participação")}</th>
                  <th className="right">{t("Volume estimado")}</th>
                  <th>{t("Último evento")}</th>
                </tr>
              </thead>
              <tbody>
                {cameras.map((c) => {
                  const muda = c.eventos === 0;
                  return (
                    <tr key={c.id}>
                      <td>
                        <div style={{ fontWeight: 500 }}>{c.nome}</div>
                        <div className="small muted mono">
                          id {c.id}{c.grupo ? ` · grupo ${c.grupo}` : ""}
                        </div>
                      </td>
                      <td>
                        {muda ? (
                          <span className="pill pill-warn">
                            <IconAlerta size={11} /> {t("sem eventos")}</span>
                        ) : (
                          <span className="pill pill-ok">
                            <IconOk size={11} /> ativa
                          </span>
                        )}
                      </td>
                      <td className="right mono">{c.eventos.toLocaleString("pt-BR")}</td>
                      <td>
                        <BarraMetrica rotulo="" valor={c.fatia_pct} limite={101} unidade="%" />
                      </td>
                      <td className="right mono small">
                        {c.bytes_estimados ? formatBytes(c.bytes_estimados) : "—"}
                      </td>
                      <td className="small">
                        {c.ultimo_evento ? (
                          formatData(c.ultimo_evento)
                        ) : (
                          <span className="muted">nunca</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="small muted">
            Fonte: banco <span className="mono">{dados.esquema.banco}</span>,
            tabelas de evento{" "}
            <span className="mono">{dados.esquema.tabelas_eventos.join(", ")}</span>.
            O esquema é descoberto automaticamente — se o FindFace for atualizado e
            a consulta falhar, use o botão de redescoberta.
          </div>
        </div>
      )}
    </>
  );
}
