<div align="center">

# FaceOps

**Painel de operação para Face Detect (NtechLab)**

Backup com recorrência · Serviços · Recursos · Terminal SSH web

[![Stack](https://img.shields.io/badge/FastAPI-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Stack](https://img.shields.io/badge/React_18-61DAFB?style=flat&logo=react&logoColor=black)](https://react.dev)
[![Stack](https://img.shields.io/badge/PostgreSQL_16-4169E1?style=flat&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Stack](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white)](https://docker.com)
[![Face Detect](https://img.shields.io/badge/Face_Detect-2.4.1-0D1F35?style=flat)](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/)

</div>

---

## Por que existe

A plataforma web do Face Detect 2.4.1 não tem tela de backup. O
procedimento oficial da NtechLab é manual, por linha de comando, e **frio**:

```bash
sudo docker-compose stop
sudo tar -cvzf ~/configs.tar.gz -C /opt/findface-multi/ configs
sudo tar -cvzf ~/data.tar.gz   -C /opt/findface-multi/ data
sudo cp /opt/findface-multi/docker-compose.yaml ~/
```

Isso **para o reconhecimento facial** e arquiva o `data/` inteiro — que
contém PostgreSQL, Tarantool, MongoDB e todas as fotos de evento. Vira
centenas de gigabytes. Não é algo que se rode todo dia, e é justamente por
isso que não existe botão para isso na interface.

O FaceOps resolve por camadas: um backup **quente** que roda de madrugada
sem parar nada, e o procedimento oficial reservado para janela de
manutenção. Junto vem o resto do que falta no dia a dia — status e reinício
de serviço, leitura de RAM/GPU/disco e terminal SSH pelo navegador.

## Funcionalidades

**Backup em três perfis** — `Config` (segundos, MB, zero downtime),
`Essencial` (minutos, GB, zero downtime: `pg_dump` de todos os bancos +
snapshot do Tarantool com os vetores faciais) e `Completo` (procedimento
oficial NtechLab, com parada). Checksum SHA-256 conferido depois da
transferência, retenção por perfil e manifesto de restore dentro do próprio
artefato.

**Recorrência programada pela web** — expressão cron editável na tela, com
atalhos prontos e tradução em português ("todo dia às 02:00"). É o que não
existe na plataforma nativa. Perfil `Completo` só agenda com aceite
explícito de janela.

**Destinos configuráveis pela web** — disco local, Azure Blob e **rclone**,
que cobre Google Drive, S3, B2, OneDrive, SFTP, WebDAV e dezenas de outros
provedores. Cadastro, teste de escrita real e retenção por destino, sem
editar `.env` nem reiniciar container. Falha em um destino não invalida os
outros.

**Configurável pela web** — identidade do painel, retenções, limites e
padrões numa aba de Configurações que se monta a partir de um catálogo.
Adicionar uma opção é uma linha no backend. Reusar a instalação em outro
cliente não exige tocar em código.

**Atualização que não atrapalha** — `atualizar.sh` consulta o painel
antes de mexer: recusa se houver backup rodando, terminal aberto ou carga
alta na máquina. Reverte sozinho se a versão nova não subir. Nunca toca
nos containers do Face Detect.

**Limpeza de eventos** — executa o procedimento oficial da NtechLab para
apagar evento antigo, que é o que realmente libera disco: num servidor
real, as fotos de evento eram 242 GB de 268 GB. A lista de opções vem do
`--help` do próprio servidor, e durante a limpeza o painel **recusa**
reiniciar container — o manual avisa que isso corromperia o banco.

**Backup do próprio painel** — protege o que nenhum outro backup cobre:
cadastro dos servidores, credenciais cifradas, agendamentos, histórico e
auditoria. A `SECRET_KEY` fica deliberadamente **fora** do artefato.

**Manutenção de disco e log** — o problema mais comum num servidor de
reconhecimento facial não é o Face Detect: é o disco raiz enchendo de log
(encontramos 99 GB de `/var/log` em operação normal, ~8 GB/dia). A tela
diagnostica, mede o crescimento e aplica a contenção — filtrando o ruído
na chegada ao rsyslog, **sem reiniciar nada do Face Detect**.

**Serviços do Face Detect** — estado, saúde, contagem de reinícios e OOM kill
de cada container, com log e reinício individual. Ações cercadas ao projeto
compose do Face Detect: o painel recusa agir em container de fora.

**Recursos sob demanda** — RAM (descontando cache, como deve ser), carga por
núcleo, GPU via `nvidia-smi` (utilização, VRAM, temperatura, watts, processos)
e ocupação de disco. Coleta no clique do botão, numa única execução SSH.
Sem Zabbix e sem polling em segundo plano.

**InTerminal** — terminal SSH real no navegador (xterm.js + PTY via
asyncssh), com sessão gravada em asciicast v2 para auditoria e queda
automática por inatividade.

**Perfis de acesso** — Observador (vê tudo, não age), Operador (reinicia e
faz backup), Técnico (soma terminal com sudo e agendamentos) e
Administrador (restore e parada de stack). Botão sem permissão não aparece.

**Cofre de credenciais** — chave PEM, senha SSH e senha de sudo cifradas com
Fernet (AES-128-CBC + HMAC-SHA256). Nunca saem pela API depois de gravadas;
a tela confirma o que está guardado pelo fingerprint.

**Identidade de host fixada** — a chave pública do servidor é lida *antes*
de qualquer credencial trafegar, e toda conexão posterior é fixada nela.
Se a chave mudar, a conexão é recusada em vez de entregar a senha de sudo a
um impostor na rede.

## Stack

| Camada | Tecnologia |
|--------|-----------|
| Backend | FastAPI + SQLAlchemy 2 async + Pydantic v2 |
| Frontend | React 18 (SPA) + xterm.js |
| Banco | PostgreSQL 16 (só do painel) |
| SSH | asyncssh (agentless — nada instalado nos servidores) |
| Agendamento | APScheduler, jobstore em memória, tabela como fonte de verdade |
| Proxy | Nginx 1.27 (gzip, CSP, WebSocket) |
| Containers | Docker Compose (3 containers) |
| Criptografia | Fernet AES-128-CBC + HMAC-SHA256 |

## Onde roda

**Fora do ambiente facial** — isolamento intencional: se um servidor
Face Detect travar, o painel continua de pé para diagnosticar e restaurar.

Duas formas, ambas suportadas:

- **Máquina Windows** com Docker Desktop (backend WSL2) — instalação
  empacotada, ver [12_INSTALACAO_WINDOWS](docs/12_INSTALACAO_WINDOWS.md)
- **VM Linux** no Hyper-V do Windows Server — mais robusta para operação
  desatendida (não depende de logon para iniciar)

**Requisito de rede em qualquer uma:** alcançar as VMs do Face Detect na porta
22 — NSG do Azure liberando o IP de saída, VPN ou Azure Bastion.

## Instalação

### Ubuntu — um comando

```bash
git clone git@github.com:ruargds/dgt-faceops.git
cd dgt-faceops
bash instalar.sh
```

Instala Docker, ajusta timezone e NTP, gera a `SECRET_KEY` e a senha do
banco, cria os diretórios, constrói, sobe e confirma que respondeu.
Pergunta só a porta e onde guardar os backups. É idempotente — rodar de
novo não quebra nada.

Primeiro acesso em `http://<ip>:8080` com **admin / admin123**. Troque a
senha imediatamente; o painel exibe faixa de aviso até isso acontecer.

**Guia completo, do zero ao backup agendado:**
[15_SOLUCAO_PRONTA](docs/15_SOLUCAO_PRONTA.md)

### Windows

Docker Desktop instalado → botão direito em `windows\instalar.bat` →
**Executar como administrador**. Detalhes e limitações em
[12_INSTALACAO_WINDOWS](docs/12_INSTALACAO_WINDOWS.md).

## Documentação

| Documento | Para que serve |
|-----------|----------------|
| [00_INDICE](docs/00_INDICE.md) | Índice e por onde começar |
| [01_ARQUITETURA](docs/01_ARQUITETURA.md) | Componentes, fluxos e decisões |
| [02_ESTRATEGIA_BACKUP](docs/02_ESTRATEGIA_BACKUP.md) | Os três perfis e o que cada um recupera |
| [03_RESTORE](docs/03_RESTORE.md) | Procedimento de restauração, passo a passo |
| [04_INSTALACAO](docs/04_INSTALACAO.md) | Instalação do zero, incluindo rede e sudoers |
| [05_PERMISSOES](docs/05_PERMISSOES.md) | Perfis, catálogo e ações destrutivas |
| [06_SEGURANCA](docs/06_SEGURANCA.md) | Cofre, pinagem de host, auditoria, superfície de ataque |
| [07_INTERMINAL](docs/07_INTERMINAL.md) | Terminal web: protocolo, gravação, limites |
| [08_API](docs/08_API.md) | Referência dos 33 endpoints |
| [09_REGRAS_DESENVOLVIMENTO](docs/09_REGRAS_DESENVOLVIMENTO.md) | Convenções e checklist de commit |
| [10_ERROS_CONHECIDOS](docs/10_ERROS_CONHECIDOS.md) | Sintoma → causa → solução |
| [11_OPERACAO_DIARIA](docs/11_OPERACAO_DIARIA.md) | Rotina, plantão e o que olhar |
| [12_INSTALACAO_WINDOWS](docs/12_INSTALACAO_WINDOWS.md) | Instalação empacotada em máquina Windows |
| [13_DESTINOS](docs/13_DESTINOS.md) | Destinos de backup: local, Azure e rclone |
| [14_MANUTENCAO](docs/14_MANUTENCAO.md) | Disco e log: diagnóstico e correção sem linha de comando |
| [**15_SOLUCAO_PRONTA**](docs/15_SOLUCAO_PRONTA.md) | **Comece aqui** — do zero ao backup agendado, em Ubuntu |
| [16_CONFIGURACOES](docs/16_CONFIGURACOES.md) | Aba Configurações: catálogo, e como reusar em outro projeto |
| [17_ATUALIZACAO](docs/17_ATUALIZACAO.md) | Buscar versão nova sem parar nem pesar serviço em operação |
| [18_LIMPEZA_DE_EVENTOS](docs/18_LIMPEZA_DE_EVENTOS.md) | Liberar disco de verdade — procedimento oficial da NtechLab |
| [19_BACKUP_DO_PAINEL](docs/19_BACKUP_DO_PAINEL.md) | Salvar o próprio painel, e por que a SECRET_KEY fica fora |
| [20_PERSISTENCIA](docs/20_PERSISTENCIA.md) | O que sobrevive a quê, e por que não há volume fantasma |
| [21_REQUISITOS](docs/21_REQUISITOS.md) | Especificações de máquina, medições de campo e dimensionamento |
| [22_REFERENCIAS](docs/22_REFERENCIAS.md) | Manuais consultados e o que cada página entregou |
| [23_MONITOR_E_CAMERAS](docs/23_MONITOR_E_CAMERAS.md) | Monitor contínuo, alertas com ação, câmeras e exportações |

## Scripts

| Script | Para quê |
|--------|----------|
| [`instalar.sh`](instalar.sh) | **Instalação completa em Ubuntu**, um comando, idempotente |
| [`atualizar.sh`](atualizar.sh) | **Atualização segura** — recusa se houver backup rodando ou carga alta; reverte sozinho se não subir |
| [`inventario.sh`](scripts/inventario.sh) | Levanta tudo de um servidor — **somente leitura**, não altera nada |
| [`descobrir_topologia.sh`](scripts/descobrir_topologia.sh) | Onde cada componente do Face Detect está, e qual perfil de backup cabe ali |
| [`ffmulti-backup.sh`](scripts/ffmulti-backup.sh) | O backup em si, enviado pelo painel via stdin — não fica no servidor |
| [`endurecer_servidor_ff.sh`](scripts/endurecer_servidor_ff.sh) | Contém o crescimento de log que enche o disco. **Simula por padrão**; só altera com `--aplicar` |
| [`provision_painel.sh`](scripts/provision_painel.sh) | Prepara a VM do painel do zero |

## Escopo

Ver [SCOPE.md](SCOPE.md) — o que o projeto faz, o que não faz e o que ficou
para depois.

---

<div align="center">
<sub>DGT · Projeto autônomo. Não depende do InfraCore nem do Camsync — segue os mesmos padrões de criação.</sub>
</div>

## Documentação, specs e skills

- **`docs/`** — 29 documentos numerados; comece pelo
  [índice](docs/00_INDICE.md).
- **`specs/`** — [invariantes](specs/invariantes.md) (o que precisa
  continuar verdadeiro, com o teste que trava cada um) e
  [pendências](specs/pendencias.md).
- **[docs/36_REFERENCIA_RAPIDA](docs/36_REFERENCIA_RAPIDA.md)** — o que já
  foi aprendido na marra, por subsistema, numa página só.

Antes de commitar:

```bash
cd backend && python tests/verificar.py     # todos os cenários, sem Postgres
```
