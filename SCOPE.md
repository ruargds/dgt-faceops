# DGT FaceOps — escopo

## O problema

Quatro VMs no Azure rodando FindFace Multi 2.4.1 (NtechLab). A plataforma
web nativa não oferece:

1. Backup pela interface — o procedimento oficial é manual, por CLI, e para
   o sistema.
2. Recorrência programada de backup — não existe agendamento nenhum.
3. Conferência de serviços ativos e reinício controlado.
4. Leitura de RAM, GPU e disco por servidor.
5. Acesso a terminal sem sair da ferramenta.

Zabbix já cobre alerta e histórico de métrica. O FaceOps **não** substitui
isso — cobre a operação que o Zabbix não faz (agir) e a leitura instantânea
no momento da investigação.

## O que faz

- Backup em três perfis (`config`, `essencial`, `completo`), sob demanda ou
  agendado, para disco local, Azure Blob e Google Drive
- Recorrência editável pela web, com validação de cron e aceite de janela
  para o perfil que causa parada
- Retenção automática por perfil, no disco do painel
- Verificação de integridade por SHA-256 depois da transferência
- Status, saúde, reinícios, OOM kill e log dos containers do FindFace
- Reinício de container individual e parada/subida do stack, com dupla
  confirmação por digitação
- Coleta sob demanda de RAM, carga, GPU (`nvidia-smi`), disco e inodes
- Análise de ocupação de disco do FindFace (onde as fotos estão indo)
- Terminal SSH web (InTerminal) com PTY real e gravação em asciicast v2
- Quatro perfis de acesso, do somente-leitura ao administrador
- Cofre cifrado para chave PEM, senha SSH e senha de sudo
- Auditoria de toda ação que muda estado em produção

## O que não faz

- **Não substitui o Zabbix.** Sem histórico de série temporal, sem trigger,
  sem alerta por e-mail ou Telegram. A coleta é no clique do botão.
- **Não instala agente** nos servidores do FindFace. Tudo por SSH.
- **Não configura o FindFace.** Câmeras, dossiês, limiares e módulos
  continuam na plataforma da NtechLab.
- **Não faz backup incremental das fotos de evento.** O perfil `essencial`
  deliberadamente deixa `findface-upload` de fora. Ver
  [docs/02_ESTRATEGIA_BACKUP.md](docs/02_ESTRATEGIA_BACKUP.md).
- **Não age em container de fora do projeto compose do FindFace.** Cerca
  intencional — sem ela o painel seria controle remoto irrestrito do Docker.
- **Não apaga backup do Azure nem do Drive.** A retenção automática mexe só
  no disco local; nuvem fica com política de ciclo de vida própria.

## Fora deste ciclo

Itens conscientemente adiados, para não inflar a primeira entrega:

- **Restore pela web.** A permissão `backups.restore` existe no catálogo e
  o artefato traz manifesto com o procedimento, mas a execução ainda é
  manual — ver [docs/03_RESTORE.md](docs/03_RESTORE.md). Restore
  automatizado sobrescreve produção; merece um ciclo próprio, com ensaio em
  servidor de teste.
- **Sincronização incremental das fotos de evento** (rsync com tiered
  storage). Depende de saber o volume real de `findface-upload` em cada VM.
- **Alerta ativo** (e-mail/Telegram quando backup falha). O Zabbix pode
  monitorar `/api/saude` e o histórico; integração dedicada fica para depois.
- **Replicação entre servidores** (`findface-multi-replication-*`). A
  plataforma tem isso nativo; o painel só observaria.
- **Multi-tenant.** O painel atende uma instalação.

## Premissas

- FindFace Multi **2.4.1**, instalado em `/opt/findface-multi`, orquestrado
  por docker-compose (v1 ou plugin v2 — detectado em execução)
- Usuário SSH com `sudo` (com senha guardada no cofre ou `NOPASSWD`)
- VM do painel alcança as VMs do FindFace na porta 22
- Rede interna ou VPN. O painel não foi endurecido para exposição direta na
  internet — se for publicar, ponha TLS e autenticação de borda na frente.

## Não-objetivos de segurança

O modelo de ameaça é **operador interno com credencial legítima**. O painel
registra tudo, cerca ações destrutivas e protege as credenciais em repouso.
Não pretende resistir a um administrador do painel mal-intencionado: quem
tem `terminal.use` e `terminal.sudo` tem root nos servidores, por
definição. Ver [docs/06_SEGURANCA.md](docs/06_SEGURANCA.md).
