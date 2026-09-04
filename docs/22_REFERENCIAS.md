# Referências

Os manuais consultados e **o que cada página entregou** ao projeto. Serve
para dois casos: conferir de onde saiu uma decisão, e saber onde voltar
quando a versão do Face Detect mudar.

---

## Manual do FindFace Multi 2.4.1 (motor do Face Detect)

Base: `https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/`

| Página | O que entregou | Onde foi usado |
|---|---|---|
| [`backup-restore.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/backup-restore.html) | O procedimento oficial de backup: `docker-compose stop`, `tar` de `configs/` e `data/`, cópia do compose | Base do perfil `completo` e do [03_RESTORE](03_RESTORE.md) |
| [`architecture.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/architecture.html) | Lista de componentes, quais usam GPU, quais guardam dado | Classificação em `stack_service.py` (`SERVICOS_GPU`, `SERVICOS_DADOS`) |
| [`status.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/status.html) | `docker ps`, `docker container inspect`, `docker container stats` | Tela de Serviços |
| [`logs.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/logs.html) | `journald.conf` com `SystemMaxUse=3G`, `daemon.json` com driver `journald`, parâmetro `LOG_LEVEL` | Tela de [Manutenção](14_MANUTENCAO.md) — a contenção segue o que o fabricante recomenda |
| [`disable_services.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/disable_services.html) | Seção `SERVICES` em `configs/findface-multi-legacy/findface-multi-legacy.py`; o que pode ser desligado | Registrado no que "pode mexer"; não implementado no painel |
| [`event-cleaner.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/event-cleaner.html) | O comando `manage.py cleanup`, os parâmetros em segundos, e o aviso de não reiniciar container durante a limpeza | Tela de [Limpeza de eventos](18_LIMPEZA_DE_EVENTOS.md) e a trava no `stack_service` |
| [`licensing.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/licensing.html) | `findface-ntls` na porta 3185, endpoint `/c2v`; a validade só aparece na interface web da NtechLab | Não há tela de licença no painel — a informação não é exposta por API |
| [`configuration.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/configuration.html) | Índice do que o administrador configura pela interface: limiares, qualidade JPEG, limpeza de evento, retenção de vídeo | Base do "o que pode mexer" |
| [`troubleshooting.html`](https://docs.ntechlab.com/projects/ffmulti/en/2.4.1/troubleshooting.html) | Índice da manutenção; foi de onde saíram os nomes reais das páginas acima | — |

### O que o manual **não** entrega

Registrado para ninguém procurar de novo:

- **Nível de log por serviço, em detalhe.** A página de logging cita
  `LOG_LEVEL` e `LOG_TO_JOURNALD`, mas não documenta os valores aceitos
  nem o efeito de cada um.
- **Endpoint de status da licença.** Só a interface web mostra validade e
  limites. O `ntls` expõe `/c2v` para geração de arquivo, não consulta.
- **Procedimento de snapshot do Tarantool para backup quente.** O manual
  só documenta o backup frio. O `box.snapshot()` que o painel usa é a
  prática padrão do Tarantool, não uma instrução da NtechLab — por isso
  está marcado como **pendente de validação em campo**.

---

## Achados fora do manual

Pesquisa em documentação de versões anteriores e material do fabricante.

### Incompatibilidade entre versões maiores

A base biométrica do Tarantool **não é compatível** entre versões maiores
do produto. A documentação de atualização de versões anteriores é
explícita: é preciso usar o procedimento de backup/restore do próprio
fabricante para migrar, não uma restauração de arquivo.

**Consequência para este projeto:** o artefato de backup é **preso à
versão**. Restaurar num Face Detect de outra versão devolve os cadastros do
PostgreSQL mas **não** o reconhecimento.

Por isso o `MANIFESTO.txt` de cada artefato registra a versão das imagens
em uso e traz esse aviso. Ver [02_ESTRATEGIA_BACKUP](02_ESTRATEGIA_BACKUP.md).

Fonte: [Update FindFace Security to 4.3](https://docs.ntechlab.com/projects/ffsecurity/en/4.3/update.html)

### Shards do Tarantool

Múltiplos `findface-tarantool-server` por host aumentam a velocidade de
busca de forma expressiva. Explica a instalação encontrada em campo: **16
shards + 16 réplicas**.

**Consequência:** o backup precisa disparar `box.snapshot()` em **todas**
as instâncias, não numa só.

Fontes: [findface-tarantool-server](https://docs.ntechlab.com/projects/ffmulti/en/1.0/tarantool-server-config.html) · [Tarantool — caso NtechLab](https://www.tarantool.io/en/cases/ntechlab/)

### Diretórios de upload em versões anteriores

`/var/lib/findface-security/uploads` e `/var/lib/ffupload/` guardavam
fotos, vídeo e miniaturas. Na 2.4.1 isso migrou para dentro de `data/`.

Útil se algum dia for preciso restaurar backup de instalação antiga.

Fonte: [Back Up and Recover Data Storages 1.1](https://docs.ntechlab.com/projects/ffmulti/en/1.1/backup-restore.html)

---

## Ferramentas e bibliotecas

| O quê | Por que foi escolhido |
|---|---|
| [asyncssh](https://asyncssh.readthedocs.io/) | SSH nativo async; casa com FastAPI e permite PTY no WebSocket sem thread |
| [APScheduler](https://apscheduler.readthedocs.io/) | Agendamento cron; usado com jobstore em memória e a tabela como fonte de verdade |
| [rclone](https://rclone.org/docs/) | Um tipo de destino cobre Drive, S3, B2, OneDrive, SFTP, WebDAV |
| [xterm.js](https://xtermjs.org/) | Terminal no navegador com PTY real |
| [asciicast v2](https://docs.asciinema.org/manual/asciicast/v2/) | Formato da gravação de sessão: uma linha JSON por evento, legível com `grep` |
| [Fernet](https://cryptography.io/en/latest/fernet/) | AES-128-CBC + HMAC-SHA256; mesmo esquema já validado no DGT InfraCore |

---

## Convenções herdadas

| De onde | O quê |
|---|---|
| **DGT InfraCore** | Paleta (navy `#0D1F35`, azul `#1A6FC4`, ciano `#00AEEF`), Heroicons outline `strokeWidth 1.5`, cofre Fernet, docs numeradas, `deploy.sh` |
| **DGT Camsync** | Superfície escura do terminal (`#0f172a` com ciano), convenção dos `.bat` no Windows |

O FaceOps é **projeto autônomo** — não depende de nenhum dos dois em
tempo de execução. O que compartilha é padrão de criação.

---

## Como atualizar este documento

Quando a versão do Face Detect mudar:

1. Trocar `2.4.1` nas URLs acima e reconferir cada página
2. Conferir se `manage.py cleanup` mudou de parâmetros — o painel lê o
   `--help` do servidor, então adapta sozinho, mas as descrições em
   `limpeza_service.py` podem ficar desatualizadas
3. Reconferir os nomes de serviço em `SERVICOS_GPU` e `SERVICOS_DADOS`
4. **Testar um restore** antes de confiar que o backup da versão nova
   funciona
