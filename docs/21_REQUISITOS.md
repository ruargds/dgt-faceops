# Requisitos de máquina

Números medidos, não estimados. Onde houver suposição, está dito.

---

## Máquina do painel

### Mínimo e recomendado

| Recurso | Mínimo absoluto | Recomendado | Por quê |
|---|---|---|---|
| CPU | 2 vCPU | 4 vCPU | O build do frontend e o cálculo de SHA-256 de artefato grande |
| **RAM** | **2 GB + swap** | **4 GB** | Ver a seção abaixo — é o requisito que mais engana |
| Disco de sistema | 20 GB | 40 GB | Imagens Docker (~1,5 GB) + banco do painel |
| Disco de backup | dimensionar | — | Ver [Dimensionamento](#dimensionamento-do-disco-de-backup) |

### RAM — o requisito que engana

Os containers em regime somam **1,6 GB** de limite:

| Container | Limite | Uso típico em repouso |
|---|---|---|
| `faceops_postgres` | 512 MB | ~60 MB |
| `faceops_backend` | 1 GB | ~180 MB |
| `faceops_frontend` | 128 MB | ~10 MB |

**Mas o pico não é o regime: é o build.** O `react-scripts build` com 1.294
pacotes npm precisa de **1,5 a 2 GB** sozinho. É o momento de maior
consumo de toda a vida da instalação, e acontece na primeira subida —
quando ninguém espera problema.

**Numa máquina de 1 GB o build morre por falta de memória**, depois de
vários minutos. Confirmado em teste.

#### Se a máquina tem pouca RAM

Swap resolve, com o custo de o build ficar lento (15 min em vez de 5):

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

Uma vez só. Depois do build, o painel roda confortável no regime de 1,6 GB.

> Para **teste do frontend**, 1 GB + swap serve. Para **uso real**, 4 GB.
> A diferença não é o painel: é a folga para o sistema operacional e para
> o cache de página do PostgreSQL.

### Sistema operacional

| Item | Requisito |
|---|---|
| Distribuição | Ubuntu 22.04 ou 24.04 |
| Docker | 24+ com plugin `compose` v2 |
| openssl | 1.1.1+ (para o certificado TLS) |
| Acesso | usuário com `sudo` |

**Testado em:** Ubuntu 24.04.4 LTS, Docker instalado pelo próprio
`instalar.sh`.

Debian recente deve funcionar — o instalador avisa que não foi testado e
segue. Outras distribuições exigem adaptar o `instalar.sh` (usa `apt`).

**Não é WSL.** Funciona, mas o Docker Desktop no Windows depende de logon
para iniciar: numa máquina que fica só ligada, os agendamentos não rodam.
Ver [12_INSTALACAO_WINDOWS](12_INSTALACAO_WINDOWS.md).

### Rede

| Requisito | Detalhe |
|---|---|
| Alcance aos servidores | Porta **22** em cada VM do FindFace |
| Porta do painel | HTTPS (padrão 8443) e HTTP (8080, só redireciona) |
| Saída para a internet | Só se usar destino em nuvem (Azure Blob, rclone) |

Verificação antes de instalar:

```bash
for ip in 10.50.153.10 10.50.153.11 10.50.153.12 10.50.155.4; do
  timeout 5 bash -c "echo > /dev/tcp/$ip/22" 2>/dev/null \
    && echo "OK      $ip" || echo "FALHOU  $ip"
done
```

---

## Servidores do FindFace

**Nada é instalado neles.** O painel é agentless.

| Requisito | Como conferir |
|---|---|
| Usuário SSH | `id -un` |
| Acesso ao Docker | `docker ps` — com ou sem `sudo`, o painel detecta |
| `sudo` | `sudo -v` — com senha guardada no cofre ou `NOPASSWD` |
| FindFace instalado | O painel detecta o caminho sozinho no teste de conexão |

**Sem `sudo`, o painel só lê métrica.** Backup, reinício de container e
limpeza de eventos não funcionam.

### Espaço para o staging

O perfil `completo` monta o artefato **no próprio servidor** antes de
transferir. Exige **60% do tamanho de `data/`** livre no diretório de
staging (configurável em Configurações → Backup).

O script aborta antes de começar se não houver espaço — não enche o disco
de produção de madrugada.

---

## Ambiente medido em campo

Números reais das VMs deste projeto, coletados em 2026-08-24.

| VM | vCPU | RAM | Disco raiz | Disco de dados | GPU |
|---|---|---|---|---|---|
| VM701629 `appserver` | 16 | 62,8 GB | 123 GB | 3,2 TB | não |
| VM701631 `dbserver` | — | — | 61 GB | 719 GB | não |
| VM701632 `extraction` | — | — | — | — | sim |
| VM701633 `integração` | — | — | — | — | não |

Instalação em `/media/STORAGE/findface-multi` nos dois servidores
FindFace — **não** no `/opt/findface-multi` da documentação.

### Ocupação medida

**VM701629 (appserver)** — `data/` = 268 GB:

| Componente | Tamanho |
|---|---|
| `findface-multi-legacy` (fotos de evento) | **242 GB** |
| `postgresql` | 581 MB |
| `mongodb` | 499 MB |
| `nats-jetstream` | 227 MB |
| `etcd` | 123 MB |
| `timescaledb` | 78 MB |

**VM701631 (dbserver)** — `data/` = 2,3 GB:

| Componente | Tamanho |
|---|---|
| `findface-tarantool-server` (16 shards + 16 réplicas) | **2,3 GB** |

**90% do volume são fotos de evento.** É o número que justifica a tela de
[limpeza de eventos](18_LIMPEZA_DE_EVENTOS.md).

### Crescimento de log medido

**~8 GB por dia** de `/var/log/syslog` no appserver, em operação normal —
log de acesso HTTP gerado pelos 80 workers da VM701633. Levou o disco
raiz a 100% (99 GB de `/var/log` em 123 GB) e o dbserver a **zero bytes
livres**.

Contido pela tela de [Manutenção](14_MANUTENCAO.md).

---

## Dimensionamento do disco de backup

Com os tamanhos acima:

| Backup | Por execução | 30 dias |
|---|---|---|
| `essencial` do appserver | ~500 MB | ~15 GB |
| `essencial` do dbserver | ~2 GB | ~60 GB |
| `config` (ambos) | poucos MB | < 1 GB |
| `painel` | poucos MB | < 1 GB |
| `completo` do appserver | ~200 GB | 2 cópias = 400 GB |

**Mínimo prático: 100 GB.** Com o perfil `completo` mensal: **600 GB**.

Se o backup for **só para nuvem**, o disco local precisa segurar **um**
artefato de cada vez (área de passagem) — desmarque "Disco do painel" nos
destinos.

### Fórmula

```
essencial:  tamanho do artefato × dias de retenção × nº de servidores
completo:   tamanho de data/ × 0,7 × cópias mantidas
```

O fator `0,7` é a compressão observada. Foto JPEG comprime pouco — não
conte com mais que isso.

---

## Onde o painel pode rodar

| Opção | A favor | Contra |
|---|---|---|
| **VM Linux dedicada** | Isolamento total; sobrevive à queda de qualquer servidor; roda desatendida | Uma VM para manter |
| Dentro de um servidor existente | Sem infra nova | Morre junto com o servidor que deveria diagnosticar; disputa CPU no build |
| Windows + Docker Desktop | Instalação em minutos | Depende de logon; licença Docker em Server |

A VM dedicada é a recomendação. As três funcionam.

> Se instalar **dentro** de um servidor em produção, o `atualizar.sh`
> recusa reconstruir com carga acima de 0,80 por núcleo — justamente para
> não competir com o que já roda ali.
