# Instalação do zero

Para instalação em **máquina Windows**, veja
[12_INSTALACAO_WINDOWS](12_INSTALACAO_WINDOWS.md) — este documento cobre a
VM Linux e os pré-requisitos comuns aos dois caminhos.

## 1. Decidir onde o painel roda

| Opção | Vantagem | Desvantagem |
|---|---|---|
| **VM Linux dedicada** (Hyper-V no Windows Server) | isolamento total; sobrevive à queda de qualquer servidor FindFace; roda desatendida com systemd | uma VM para manter |
| Docker Desktop no Windows | instalação em minutos; sem VM para gerenciar | depende de logon para iniciar; licença Docker em Server |
| Dentro do `vm-appserver` | sem infra nova | morre junto com o servidor que deveria diagnosticar; disputa RAM com o FindFace |

A VM Linux dedicada é a recomendação. As três funcionam.

## 2. Rede — resolva isto primeiro

A máquina do painel precisa alcançar cada servidor FindFace na porta 22.

```bash
for ip in 10.0.1.10 10.0.1.11 10.0.1.12 10.0.1.13; do
    timeout 5 bash -c "echo > /dev/tcp/$ip/22" 2>/dev/null \
        && echo "OK      $ip" || echo "FALHOU  $ip"
done
```

Se falhar:

| Cenário | Solução |
|---|---|
| VMs com IP público | Regra de entrada no NSG do Azure liberando 22 para o IP de saída do painel (`curl ifconfig.me`) |
| VMs só com IP privado | VPN Site-to-Site, Azure Bastion, ou painel numa VM dentro da VNet |
| IP de saída dinâmico | IP fixo ou VPN — regra de NSG com IP que muda quebra sozinha |

> Não libere `0.0.0.0/0` na porta 22 "só para testar". Um servidor de
> reconhecimento facial com SSH aberto para a internet entra em botnet de
> força bruta em horas.

## 3. Preparar os servidores do FindFace

Nada é instalado. É preciso apenas:

### Usuário SSH com docker e sudo

```bash
# No servidor FindFace
sudo usermod -aG docker azureuser
# relogar para o grupo valer
```

Conferir:

```bash
docker ps                # sem sudo — precisa funcionar
sudo -v                  # sudo precisa funcionar
ls /opt/findface-multi   # o FindFace está aqui?
```

### sudo

Duas opções:

**a) Senha de sudo guardada no cofre do painel** — mais controlado. O painel
cifra a senha com Fernet e a passa por stdin (`sudo -S`), então ela nunca
aparece no `ps` do servidor.

**b) `NOPASSWD` restrito:**

```bash
sudo tee /etc/sudoers.d/faceops > /dev/null <<'EOF'
# FaceOps — operação do FindFace Multi
azureuser ALL=(root) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose, /usr/bin/tar, /bin/bash
EOF
sudo chmod 0440 /etc/sudoers.d/faceops
sudo visudo -c
```

> `NOPASSWD` para `/bin/bash` é root completo, na prática. A lista acima
> documenta a intenção e evita acidente — não contém um operador
> mal-intencionado. Se isso importa no seu modelo de ameaça, use a opção (a).

### Chave SSH

Prefira chave a senha. Se ainda não houver uma dedicada ao painel:

```bash
# Na máquina do painel
ssh-keygen -t ed25519 -f ~/.ssh/faceops -C "dgt-faceops"

# Publicar em cada servidor
ssh-copy-id -i ~/.ssh/faceops.pub azureuser@10.0.1.10
```

O conteúdo de `~/.ssh/faceops` (a chave **privada**) é o que se cola no
cadastro do servidor no painel. Ela é cifrada com Fernet antes de ir para o
banco e nunca mais sai pela API.

## 4. Provisionar a VM do painel

```bash
bash scripts/provision_painel.sh
```

Faz: pacotes, timezone `America/Sao_Paulo`, NTP brasileiro (com fallback
para chrony), Docker, limites de log do Docker, `ufw` e o teste de alcance
aos servidores.

> O NTP não é detalhe. Relógio errado quebra TLS, desalinha o cron dos
> agendamentos e datilografa backups com data errada — e falha em silêncio.

## 5. Instalar o painel

```bash
git clone git@github.com:ruargds/dgt-faceops.git
cd dgt-faceops
cp .env.example .env
```

Edite o `.env`. O mínimo obrigatório:

```bash
# Gere e cole:
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

| Variável | Observação |
|---|---|
| `SECRET_KEY` | **crítica** — dela deriva o cofre. Trocar depois torna as credenciais guardadas ilegíveis |
| `POSTGRES_PASSWORD` | senha do banco do painel |
| `PORTA_HTTP` | padrão 8080 |
| `ADMIN_USER` / `ADMIN_PASSWORD` | `admin` / `admin123` de fábrica |
| `LOCAL_BACKUP_DIR` | dentro do container é `/data/backups`; o mapeamento está no compose |

Opcional, conforme os destinos que for usar:

```bash
AZURE_STORAGE_CONNECTION_STRING=...
AZURE_BLOB_CONTAINER=faceops-backups

RCLONE_REMOTE=gdrive
RCLONE_PATH=FaceOps/backups
```

Para o Google Drive, gere o `rclone.conf` e coloque em `./rclone/`:

```bash
rclone config          # crie um remote chamado "gdrive"
cp ~/.config/rclone/rclone.conf ./rclone/
```

Subir:

```bash
bash deploy.sh --build
```

O `deploy.sh` recusa subir se a `SECRET_KEY` ainda for o valor de exemplo —
cofre com segredo público não protege nada.

## 6. Disco de backup

Monte um disco de verdade em `data/backups`. Dimensione:

```
essencial:  tamanho do artefato × dias de retenção × nº de servidores
completo:   tamanho de data/ × 0,7 × cópias × nº de servidores
```

O tamanho de `data/` você descobre em **Recursos → Analisar**, depois de
cadastrar os servidores. Até lá, reserve para o `essencial`.

Se o backup for só para nuvem, o disco local precisa segurar um artefato de
cada vez — desmarque "Disco do painel" nos destinos.

## 7. Primeiro acesso

`http://<endereço>:8080` — **admin / admin123**.

1. Troque a senha (a faixa de aviso fica até isso acontecer)
2. **Servidores → Cadastrar servidor**
   - Nome, papel, endereço, porta, usuário SSH
   - **Ler chave do servidor** → confirme o fingerprint
   - Cole a chave PEM (ou a senha)
   - Senha de sudo, se não houver `NOPASSWD`
3. **Testar conexão** — precisa vir verde com `sudo: sim`, `docker: sim`,
   `FindFace: sim`
4. Repita para os outros servidores

## 8. Validar antes de confiar

Em ordem, e sem pular:

```
[ ] Testar conexão verde nos quatro servidores
[ ] Recursos → Atualizar traz RAM, disco e GPU onde há
[ ] Serviços lista os containers do FindFace, com o projeto compose certo
[ ] Backup config executado com sucesso
[ ] Backup essencial executado; log lista os bancos; tarantool_metodo = tarantoolctl
[ ] Recursos → Analisar mediu data/ em cada servidor
[ ] Agendamentos criados com base nessa medição
[ ] InTerminal abre e responde a comando
[ ] Auditoria registrou todas as ações acima
[ ] Restore de teste feito em VM separada (docs/03_RESTORE.md)
```

O último item é o que separa "instalado" de "confiável".

## 9. Usuários

**Usuários** → crie contas nominais. Evite conta compartilhada: a auditoria
registra quem fez o quê, e "admin" não é o nome de ninguém.

Sugestão de atribuição:

| Quem | Perfil |
|---|---|
| Gestão, cliente, consulta | Observador |
| Plantão / suporte N1 | Operador |
| Equipe de infraestrutura | Técnico |
| Responsável pelo ambiente | Administrador |

Ver [05_PERMISSOES](05_PERMISSOES.md).
