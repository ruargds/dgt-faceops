# DGT FaceOps — índice da documentação

## Por onde começar

| Se você é… | Leia nesta ordem |
|------------|------------------|
| **Primeira vez aqui** | [15_SOLUCAO_PRONTA](15_SOLUCAO_PRONTA.md) — do zero ao backup agendado |
| **Quem vai instalar (Linux)** | [04_INSTALACAO](04_INSTALACAO.md) → [06_SEGURANCA](06_SEGURANCA.md) → [02_ESTRATEGIA_BACKUP](02_ESTRATEGIA_BACKUP.md) |
| **Quem vai instalar (Windows)** | [12_INSTALACAO_WINDOWS](12_INSTALACAO_WINDOWS.md) → [04_INSTALACAO](04_INSTALACAO.md) (seções de rede e sudoers) |
| **Quem vai operar / plantão** | [11_OPERACAO_DIARIA](11_OPERACAO_DIARIA.md) → [02_ESTRATEGIA_BACKUP](02_ESTRATEGIA_BACKUP.md) → [14_MANUTENCAO](14_MANUTENCAO.md) → [10_ERROS_CONHECIDOS](10_ERROS_CONHECIDOS.md) |
| **Quem precisa restaurar agora** | [03_RESTORE](03_RESTORE.md) — vá direto, o resto espera |
| **Quem vai desenvolver** | [01_ARQUITETURA](01_ARQUITETURA.md) → [09_REGRAS_DESENVOLVIMENTO](09_REGRAS_DESENVOLVIMENTO.md) → [08_API](08_API.md) |
| **Quem vai auditar** | [06_SEGURANCA](06_SEGURANCA.md) → [05_PERMISSOES](05_PERMISSOES.md) → [07_INTERMINAL](07_INTERMINAL.md) |

## Documentos

### Fundamentos

- **[01_ARQUITETURA](01_ARQUITETURA.md)** — componentes, fluxo de um backup
  de ponta a ponta, modelo de dados e as decisões que valem explicação
  (por que agentless, por que jobstore em memória, por que ticket no
  WebSocket).

- **[02_ESTRATEGIA_BACKUP](02_ESTRATEGIA_BACKUP.md)** — o documento central
  do projeto. Por que o backup oficial da NtechLab não serve para
  recorrência, o que cada um dos três perfis leva, o que cada um recupera e
  o que fica de fora.

- **[03_RESTORE](03_RESTORE.md)** — restauração passo a passo, por perfil.
  Inclui o que fazer quando só existe o backup `essencial` e as fotos de
  evento foram perdidas.

### Operação

- **[15_SOLUCAO_PRONTA](15_SOLUCAO_PRONTA.md)** — o caminho completo em
  nove partes: instalar o painel em Ubuntu, preparar os servidores,
  cadastrar, dimensionar o disco, validar, agendar, conter o log e
  operar. Com os valores reais do ambiente levantado.

- **[04_INSTALACAO](04_INSTALACAO.md)** — do zero: VM, rede, NSG do Azure,
  sudoers nos servidores do FindFace, cadastro do primeiro host, validação.

- **[12_INSTALACAO_WINDOWS](12_INSTALACAO_WINDOWS.md)** — instalação
  empacotada em máquina Windows (Docker Desktop + WSL2): requisitos de
  hardware, rede, os `.bat` de operação e as limitações de execução
  desatendida.

- **[11_OPERACAO_DIARIA](11_OPERACAO_DIARIA.md)** — rotina recomendada, o
  que olhar no plantão, como interpretar reinícios e OOM kill, e a ordem de
  investigação quando o reconhecimento para.

- **[10_ERROS_CONHECIDOS](10_ERROS_CONHECIDOS.md)** — sintoma, causa,
  solução. Cresce com o uso; registre aqui o que custou tempo para achar.

- **[13_DESTINOS](13_DESTINOS.md)** — onde os backups vão parar: disco
  local, Azure Blob e rclone (Drive, S3, B2, OneDrive, SFTP…). Como
  configurar, testar e combinar destinos, e por que a retenção só age no
  local.

- **[14_MANUTENCAO](14_MANUTENCAO.md)** — disco e log pela web. Por que o
  disco raiz enche num servidor de reconhecimento facial, como medir o
  crescimento, e como conter sem reiniciar nada do FindFace.

- **[16_CONFIGURACOES](16_CONFIGURACOES.md)** — a aba Configurações: as
  três camadas (banco > `.env` > padrão), como o catálogo faz a tela se
  montar sozinha, e o que deliberadamente não fica lá.

- **[17_ATUALIZACAO](17_ATUALIZACAO.md)** — buscar versão nova nos
  servidores sem parar, travar ou pesar nada em operação. As três
  garantias, a reversão automática e os códigos de saída.

- **[18_LIMPEZA_DE_EVENTOS](18_LIMPEZA_DE_EVENTOS.md)** — a ação que
  realmente libera disco, e a mais destrutiva do painel. Procedimento
  oficial da NtechLab, com as três proteções que o cercam.

- **[19_BACKUP_DO_PAINEL](19_BACKUP_DO_PAINEL.md)** — o painel protegia
  quatro servidores e nada protegia o painel. O que se perderia, o que
  entra no artefato e por que a `SECRET_KEY` fica fora.

- **[20_PERSISTENCIA](20_PERSISTENCIA.md)** — documento de revisão: o que
  sobrevive a cada operação, onde cada dado mora, por que não há volume
  fantasma e qual é o único comando que apaga o banco.

- **[23_MONITOR_E_CAMERAS](23_MONITOR_E_CAMERAS.md)** — monitor contínuo
  sem onerar servidor, alertas com ação, câmeras e licenciamento do
  FindFace, e as exportações em CSV.

### Segurança e acesso

- **[05_PERMISSOES](05_PERMISSOES.md)** — os quatro perfis, o catálogo
  completo de permissões e quais ações exigem dupla confirmação.

- **[06_SEGURANCA](06_SEGURANCA.md)** — cofre Fernet, pinagem de chave de
  host, o que é registrado em auditoria, superfície de ataque e limites
  honestos do modelo.

- **[07_INTERMINAL](07_INTERMINAL.md)** — terminal web: protocolo do
  WebSocket, ticket de uso único, formato da gravação e como reproduzir.

### Referência

- **[21_REQUISITOS](21_REQUISITOS.md)** — o que a máquina precisa ter.
  Números medidos, incluindo a armadilha da RAM: o pico não é o regime
  (1,6 GB), é o build (2 GB), e ele acontece na primeira subida.
  Traz também a ocupação real medida nas VMs e o dimensionamento do
  disco de backup.

- **[22_REFERENCIAS](22_REFERENCIAS.md)** — cada página do manual da
  NtechLab e o que ela entregou ao projeto, o que o manual **não**
  entrega, e os achados fora dele — inclusive a incompatibilidade da base
  do Tarantool entre versões maiores.

### Desenvolvimento

- **[08_API](08_API.md)** — referência dos endpoints, com corpo de exemplo
  e permissão exigida por rota.

- **[09_REGRAS_DESENVOLVIMENTO](09_REGRAS_DESENVOLVIMENTO.md)** — convenções
  de backend e frontend, armadilhas já encontradas e checklist antes de
  commitar.

## Fora da pasta docs

- **[README.md](../README.md)** — visão geral, stack e instalação rápida
- **[SCOPE.md](../SCOPE.md)** — o que faz, o que não faz, o que foi adiado
- **[.env.example](../.env.example)** — todas as variáveis, comentadas
- **`/api/docs`** — OpenAPI interativo do painel em execução

## Referência externa

- [FindFace Multi 2.4.1 — documentação oficial](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/index.html)
- [Backup e restore oficial](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/backup-restore.html)
- [Arquitetura e componentes](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/architecture.html)
- [Verificação de status de componente](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/status.html)
