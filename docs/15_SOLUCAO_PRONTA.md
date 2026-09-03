# Solução pronta — Ubuntu

Guia completo, do zero ao backup rodando. Feito para o ambiente real
levantado neste projeto: quatro VMs no Azure, FindFace Multi 2.4.1
distribuído.

---

## Parte 1 — Instalar o painel

### O que é preciso

Uma máquina Ubuntu 22.04 ou 24.04, **fora do ambiente facial**, que
alcance as VMs na porta 22.

> **Nesta instalação não foi assim.** O painel está em `/opt/.faceops`,
> **dentro** da VM701633 (`vm-integracao`) — uma das quatro máquinas que
> ele monitora. Funciona, e é onde se roda o `atualizar.sh`; mas significa
> que o build, o `du` sob demanda e o backup agendado disputam CPU e disco
> com os 80 workers que rodam lá. Ao dimensionar qualquer coleta nova,
> conte o custo como se fosse em servidor de produção — porque é.

| Recurso | Mínimo | Recomendado |
|---|---|---|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 4 GB | 8 GB |
| Disco de sistema | 20 GB | 40 GB |
| Disco de backup | ver Parte 4 | — |

### Confirme a rede antes

```bash
for ip in 10.50.153.10 10.50.153.11 10.50.153.12 10.50.155.4; do
  timeout 5 bash -c "echo > /dev/tcp/$ip/22" 2>/dev/null \
    && echo "OK      $ip" || echo "FALHOU  $ip"
done
```

Tudo `OK` → siga. Algum `FALHOU` → resolva a rota antes (NSG do Azure,
VPN ou Bastion). Ver [04_INSTALACAO](04_INSTALACAO.md).

### Instale

```bash
cd ~
# copie a pasta do projeto para cá, ou:
git clone git@github.com:ruargds/dgt-faceops.git
cd dgt-faceops

bash instalar.sh
```

Um comando. Ele instala Docker, ajusta timezone e NTP, gera a
`SECRET_KEY` e a senha do banco, cria os diretórios, constrói as imagens,
sobe tudo e confirma que respondeu. Pergunta só duas coisas: a porta e
onde guardar os backups.

Ao final: `http://<ip>:8080`, com **admin / admin123**.

**Troque a senha imediatamente.** A faixa de aviso fica até isso
acontecer.

### Guarde o `.env`

```bash
sudo cp .env /caminho/seguro/faceops.env.backup
```

Dele deriva o cofre que cifra as chaves SSH. Perdê-lo não perde dado
nenhum, mas obriga a recadastrar a credencial dos quatro servidores.

---

## Parte 2 — Preparar os servidores

Nada é instalado neles. Em **cada uma das quatro VMs**, confirme:

```bash
docker ps          # precisa funcionar (com ou sem sudo)
sudo -v            # sudo precisa funcionar
```

Se o `docker ps` exigir sudo, tudo bem — o painel detecta e usa sudo
sozinho. Se preferir resolver na origem:

```bash
sudo usermod -aG docker azureadmin   # e relogar
```

### Chave SSH dedicada

Na máquina do painel:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/faceops -C "dgt-faceops" -N ""
for ip in 10.50.153.10 10.50.153.11 10.50.153.12 10.50.155.4; do
  ssh-copy-id -i ~/.ssh/faceops.pub azureadmin@$ip
done
cat ~/.ssh/faceops     # esta é a chave que vai no cadastro
```

Uma chave por ambiente é melhor que reaproveitar chave pessoal: some do
painel, some o acesso, sem afetar mais ninguém.

---

## Parte 3 — Cadastrar os servidores

**Servidores → Cadastrar servidor**, quatro vezes. Os valores do
ambiente levantado:

| Nome | Papel | Endereço | Usuário | O que roda |
|---|---|---|---|---|
| `vm-appserver` | Aplicação | `10.50.153.10` | `azureadmin` | FindFace app, PostgreSQL, MongoDB, etcd, **fotos de evento (242 GB)** |
| `vm-extraction` | Extração / GPU | `10.50.153.11` | `azureadmin` | Extração facial, GPU |
| `vm-integracao` | Outro | `10.50.153.12` | `azureadmin` | Aplicação DGT — 80 workers, Grafana, cloudflared |
| `vm-dbserver` | Banco de dados | `10.50.155.4` | `azureadmin` | **Tarantool, 16 shards — os vetores faciais** |

Em cada um:

1. **Ler chave do servidor** → confira o fingerprint que aparece
2. Cole a chave PEM (`~/.ssh/faceops`)
3. Senha de sudo, se não houver `NOPASSWD`
4. Deixe o caminho de instalação em branco — o painel detecta sozinho
5. Salve e clique em **Testar conexão**

O teste precisa vir verde com `sudo: sim` e `docker: sim`. Ele também
corrige o caminho de instalação automaticamente: nestes servidores o
FindFace está em `/media/STORAGE/findface-multi`, não no
`/opt/findface-multi` da documentação.

---

## Parte 4 — Destinos

**Destinos**. Um destino local já existe, criado na instalação.

### Dimensionar o disco

Com base nos tamanhos medidos:

| Backup | Tamanho por execução | 30 dias |
|---|---|---|
| `essencial` do appserver | ~500 MB | ~15 GB |
| `essencial` do dbserver | ~2 GB | ~60 GB |
| `config` (ambos) | poucos MB | < 1 GB |
| `completo` do appserver | ~200 GB | 2 cópias = 400 GB |

**Mínimo prático: 100 GB.** Com o perfil completo mensal: **600 GB**.

Ajuste a retenção do destino local para 30 dias.

### Destino externo

**Novo destino**, e escolha:

- **Azure Blob** se já houver conta de armazenamento — container privado,
  camada `Cool`
- **rclone** para qualquer outro provedor (S3, B2, Drive, OneDrive,
  SFTP). Gere com `rclone config` e cole o bloco

Marque local e externo como **padrão**, e clique em **Testar** nos dois.
O teste grava um arquivo pequeno de verdade — credencial válida não
garante permissão de escrita.

Detalhes em [13_DESTINOS](13_DESTINOS.md).

---

## Parte 5 — Validar antes de agendar

Nesta ordem, sem pular:

```
[ ] Testar conexão verde nos 4 servidores
[ ] Recursos → Atualizar traz RAM e disco; GPU aparece no vm-extraction
[ ] Serviços lista os containers do FindFace no appserver e no dbserver
[ ] Manutenção → Diagnosticar no appserver (mede o crescimento do log)
[ ] Backups → Novo backup → perfil 'config' no vm-appserver
[ ] O backup terminou em sucesso, com destinos verdes
[ ] Backups → perfil 'essencial' no vm-appserver
[ ] Backups → perfil 'essencial' no vm-dbserver
```

No log do `essencial` do dbserver, procure a linha:

```
FACEOPS:tarantool_metodo=tarantoolctl
```

**Se aparecer `copia-direta` em vez disso**, o `box.snapshot()` não pôde
ser disparado. Os arquivos foram copiados assim mesmo e o Tarantool
reaplica os xlogs no restore, mas a consistência não fica garantida.
Registre o que apareceu — é o único ponto do backup que ainda não foi
validado em campo.

---

## Parte 6 — Agendamentos

**Agendamentos → Novo agendamento**, cinco vezes:

| Nome | Servidor | Perfil | Cron | Retenção |
|---|---|---|---|---|
| Essencial diário — app | `vm-appserver` | `essencial` | `0 2 * * *` | 30 |
| Essencial diário — db | `vm-dbserver` | `essencial` | `15 2 * * *` | 30 |
| Config 6h — app | `vm-appserver` | `config` | `0 */6 * * *` | 90 |
| Config 6h — db | `vm-dbserver` | `config` | `10 */6 * * *` | 90 |
| Completo mensal — app | `vm-appserver` | `completo` | `0 3 1 * *` | 180 |

Os horários são deslocados de propósito (02:00 e 02:15) para os dois
uploads não competirem pela mesma banda de saída.

O **completo** exige marcar o aceite de janela de manutenção — ele para
o FindFace durante a cópia.

### O par que não pode ser separado

Os dois `essencial` são um par:

- O do **appserver** salva cadastros, dossiês, usuários e câmeras
- O do **dbserver** salva os **vetores faciais**

**Restaurar só um não devolve o reconhecimento.** Os cadastros aparecem
na tela, mas ninguém é reconhecido. Sempre restaure os dois do mesmo dia.

O `vm-extraction` e o `vm-integracao` não recebem agendamento: o primeiro
é worker de GPU sem estado, e o segundo foi avaliado como totalmente
recriável.

---

## Parte 7 — Conter o log

O appserver gera cerca de **8 GB de log por dia** — log de acesso HTTP em
operação normal, gerado pelos 80 workers do `vm-integracao` postando
detecções.

**Manutenção → selecione o `vm-appserver` → Diagnosticar.**

Depois:

1. **Ver o que será alterado** — mostra os três arquivos, sem escrever nada
2. **Aplicar contenção** — filtra o ruído na chegada ao rsyslog

Nada do FindFace reinicia. Só `rsyslog` e `journald`, ambos instantâneos.

Se ainda houver `syslog.1` grande sobrando, use **Arquivar log antigo**:
move para o disco com folga e comprime lá. Nada é apagado.

Repita no `vm-dbserver`. Detalhes em [14_MANUTENCAO](14_MANUTENCAO.md).

---

## Parte 8 — Usuários

**Usuários**. Crie contas nominais — a auditoria registra quem fez o quê,
e "admin" não é o nome de ninguém.

| Quem | Perfil | Pode |
|---|---|---|
| Gestão, cliente | Observador | Ver tudo, não executa nada |
| Plantão / N1 | Operador | Reiniciar container, disparar backup, terminal |
| Infraestrutura | Técnico | + sudo no terminal, agendamentos, manutenção |
| Responsável | Administrador | Tudo, inclusive parar stack e restore |

Ver [05_PERMISSOES](05_PERMISSOES.md).

---

## Parte 9 — Rotina

**Todo dia, 2 minutos:** abra o **Painel**. Três coisas — ponto verde em
todos, último backup de ontem em verde, disco do painel com espaço.

**Toda semana:** confira reinícios de container em Serviços e o
crescimento do log em Manutenção.

**Todo mês:** confirme que o `completo` rodou, e faça um **restore de
teste** em VM separada.

> Backup nunca restaurado não é backup. É esperança com nome técnico.

Ver [11_OPERACAO_DIARIA](11_OPERACAO_DIARIA.md) e
[03_RESTORE](03_RESTORE.md).

---

## Atualizar o painel depois

```bash
cd /opt/.faceops
bash atualizar.sh
```

`/opt/.faceops` é onde esta instalação vive — ver
[36_REFERENCIA_RAPIDA](36_REFERENCIA_RAPIDA.md) e
[17_ATUALIZACAO](17_ATUALIZACAO.md). O `deploy.sh --build` continua
valendo para subir a primeira vez ou depois de mexer no `.env`; para
buscar versão nova, o `atualizar.sh` é quem confere carga, trabalho em
curso e reverte sozinho se não subir.

Preserva `.env`, banco, backups e gravações. As migrações rodam sozinhas
na subida.

---

## Se algo não funcionar

```bash
sudo docker compose logs --tail 100 backend
curl http://localhost:8080/api/saude
```

Sintoma → causa → solução em [10_ERROS_CONHECIDOS](10_ERROS_CONHECIDOS.md),
que inclui os cinco casos reais encontrados neste ambiente.

---

## O que fica fora deste escopo

Dito com clareza para não gerar expectativa errada:

- **Restore automatizado pela web** — a permissão existe, a execução é
  manual. Sobrescrever produção merece um ciclo próprio, com ensaio.
- **Backup incremental das fotos de evento** — os 242 GB só entram no
  perfil `completo`. Sincronização incremental é trabalho futuro.
- **Alerta ativo** — o painel mostra, não avisa. Use o Zabbix apontando
  para `/api/saude`, `vfs.fs.size[/,pfree]` e
  `vfs.file.size[/var/log/syslog]`.
- **O painel não substitui o Zabbix.** A coleta é sob demanda, no clique
  do botão, e não guarda histórico.
