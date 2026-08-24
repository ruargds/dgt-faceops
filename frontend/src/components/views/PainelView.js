import React, { useCallback, useEffect, useState } from "react";
import { api, formatBytes, formatData } from "../../api";
import { Carregando, Erro, Estatistica, Vazio } from "../Comuns";
import { IconAlerta, IconAtualizar, IconGPU } from "../Icons";

const PAPEIS = {
  appserver: "Aplicação",
  dbserver: "Banco de dados",
  extraction: "Extração / GPU",
  ftpserver: "FTP / arquivos",
  outro: "Servidor",
};

export default function PainelView() {
  const [dados, setDados] = useState(null);
  const [erro, setErro] = useState("");
  const [carregando, setCarregando] = useState(true);

  const carregar = useCallback(async () => {
    setCarregando(true);
    setErro("");
    try {
      setDados(await api.painel());
    } catch (ex) {
      setErro(ex.message);
    } finally {
      setCarregando(false);
    }
  }, []);

  useEffect(() => {
    carregar();
  }, [carregar]);

  const armazenamento = dados && dados.armazenamento_painel;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="page-title">Painel</div>
          <div className="page-sub">
            Situação dos servidores FindFace Multi e do último backup de cada um
          </div>
        </div>
        <div className="page-actions">
          <button className="btn btn-secondary" onClick={carregar} disabled={carregando}>
            <IconAtualizar size={15} /> Atualizar
          </button>
        </div>
      </div>

      <Erro mensagem={erro} onTentar={carregar} />

      {carregando && !dados && <Carregando texto="Consultando os servidores…" />}

      {dados && (
        <div className="stack-v">
          {armazenamento && (
            <div className="grid-stats">
              <Estatistica
                rotulo="Disco de backup do painel"
                valor={`${formatBytes(armazenamento.livre_bytes)} livres`}
                sub={`${formatBytes(armazenamento.usado_bytes)} de ${formatBytes(
                  armazenamento.total_bytes
                )} em uso — ${armazenamento.caminho}`}
                pct={armazenamento.percentual}
              />
              <Estatistica
                rotulo="Servidores"
                valor={`${dados.servidores.filter((s) => s.ativo).length} ativos`}
                sub={`${dados.servidores.length} cadastrados no total`}
              />
              <Estatistica
                rotulo="Serviços com problema"
                valor={dados.servidores.reduce(
                  (t, s) => t + ((s.servicos && s.servicos.com_problema) || 0),
                  0
                )}
                sub="Containers parados ou marcados como unhealthy"
              />
            </div>
          )}

          {dados.servidores.length === 0 ? (
            <Vazio titulo="Nenhum servidor cadastrado">
              Cadastre as VMs do FindFace Multi em <strong>Servidores</strong> para
              começar a operar por aqui.
            </Vazio>
          ) : (
            <div className="grid-cards">
              {dados.servidores.map((s) => (
                <CartaoServidor key={s.host_id} s={s} />
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}

function CartaoServidor({ s }) {
  const serv = s.servicos || {};
  const backup = s.ultimo_backup;

  const discosCriticos = serv.discos_criticos || [];

  let classeDot = "dot-idle";
  let textoStatus = "Nunca contactado";
  if (!s.ativo) {
    classeDot = "dot-idle";
    textoStatus = "Desativado";
  } else if (s.status_conexao === "erro") {
    classeDot = "dot-err";
    textoStatus = "Sem conexão";
  } else if (discosCriticos.length > 0) {
    // Disco cheio vem antes de serviço com problema na ordem de leitura:
    // e o que causa o resto. Container so aparece quebrado depois que o
    // banco ja parou de escrever.
    classeDot = "dot-err";
    textoStatus = `Disco em ${discosCriticos[0].percentual}%`;
  } else if (serv.com_problema > 0) {
    classeDot = "dot-warn";
    textoStatus = `${serv.com_problema} serviço(s) com problema`;
  } else if (serv.total > 0) {
    classeDot = "dot-ok";
    textoStatus = `${serv.rodando} de ${serv.total} serviços rodando`;
  }

  return (
    <div className="card">
      <div className="stack-h" style={{ marginBottom: 4 }}>
        <span className={`dot ${classeDot}`} />
        <strong style={{ fontSize: 15, color: "var(--navy)" }}>{s.nome}</strong>
        {s.tem_gpu && (
          <span className="pill pill-info">
            <IconGPU size={12} /> GPU
          </span>
        )}
      </div>

      <div className="small muted" style={{ marginBottom: 12 }}>
        {PAPEIS[s.papel] || s.papel} · <span className="mono">{s.endereco}</span>
      </div>

      <div className="small" style={{ marginBottom: 10 }}>{textoStatus}</div>

      {discosCriticos.length > 0 && (
        <div
          className="card card-tight"
          style={{ background: "var(--red-bg)", borderColor: "#f3b6b6", marginBottom: 10 }}
        >
          {discosCriticos.map((d) => (
            <div
              key={d.ponto}
              className="stack-h small"
              style={{ color: "#8c1c1c", gap: 6 }}
            >
              <IconAlerta size={13} />
              <span className="mono">{d.ponto}</span>
              <span>
                {d.percentual}% — só {formatBytes(d.livre_bytes)} livres
              </span>
            </div>
          ))}
        </div>
      )}

      {serv.erro && (
        <div className="small" style={{ color: "var(--red)", marginBottom: 10 }}>
          {serv.erro}
        </div>
      )}

      <div
        style={{
          borderTop: "1px solid var(--border)",
          paddingTop: 10,
          display: "grid",
          gap: 4,
        }}
      >
        <div className="stack-h" style={{ justifyContent: "space-between" }}>
          <span className="small muted">Último backup</span>
          {backup ? (
            <span className={`pill ${backup.status === "sucesso" ? "pill-ok" : backup.status === "falha" ? "pill-err" : "pill-info"}`}>
              {backup.perfil}
            </span>
          ) : (
            <span className="pill pill-err">nenhum</span>
          )}
        </div>
        {backup && (
          <div className="small muted">
            {formatData(backup.em)} · {formatBytes(backup.tamanho_bytes)}
          </div>
        )}
        <div className="small muted">
          Último contato: {formatData(s.ultimo_contato)}
        </div>
      </div>
    </div>
  );
}
