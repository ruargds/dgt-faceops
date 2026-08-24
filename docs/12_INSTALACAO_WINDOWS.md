# Instalação em máquina Windows

O painel roda em containers Linux. No Windows isso funciona pelo **Docker
Desktop com backend WSL2** — o WSL2 já é a VM Linux, então não é preciso
criar uma no Hyper-V à mão.

## O que é preciso

### Sistema operacional

| Requisito | Mínimo | Observação |
|---|---|---|
| Windows | 10 (2004+) / 11, ou Server 2019 / 2022 / 2025 | 64 bits |
| Virtualização | habilitada na BIOS/UEFI | Intel VT-x ou AMD-V |
| WSL2 | instalado | `wsl --install` e reiniciar |
| Docker Desktop | versão atual | backend WSL2, não Hyper-V |

Em **Windows Server**, o Docker Desktop exige licença comercial da Docker
para empresas acima do limite de isenção. A alternativa sem licença é rodar
`docker` + `docker compose` direto dentro de uma distro WSL2 (Ubuntu) — o
mesmo `deploy.sh` funciona lá.

### Hardware

| Recurso | Mínimo | Recomendado | Por quê |
|---|---|---|---|
| CPU | 2 vCPU | 4 vCPU | build da imagem e checksum de arquivo grande |
| RAM | 4 GB | 8 GB | os containers pedem ~1,6 GB; o WSL2 reserva mais |
| Disco (sistema) | 20 GB | 40 GB | imagens Docker + banco do painel |
| Disco (backups) | **dimensionar** | — | ver abaixo |

**O disco de backup é a decisão que importa.** Calcule antes:

```
Perfil essencial:  tamanho de um artefato × dias de retenção × nº de servidores
Perfil completo:   tamanho de data/ × 0,7 × cópias mantidas × nº de servidores
```

Você descobre o tamanho de `data/` na tela **Recursos → Analisar**, depois
que o painel estiver de pé e os servidores cadastrados. Até lá, reserve
espaço para o `essencial` e deixe o `completo` para depois da medição.

Se o backup for direto para Azure Blob, o disco local só precisa segurar
**um** artefato de cada vez (área de passagem). Nesse caso desmarque o
destino "Disco do painel" e o arquivo é descartado após o upload.

### Rede

Este é o ponto que trava instalação com mais frequência.

A máquina Windows precisa **alcançar as VMs do Azure na porta 22**. Verifique
antes de instalar, no PowerShell:

```powershell
Test-NetConnection -ComputerName <ip-da-vm> -Port 22
```

`TcpTestSucceeded : True` em todas as quatro? Pode seguir. Se não:

| Situação | O que fazer |
|---|---|
| VMs com IP público | Liberar a porta 22 no NSG do Azure para o IP de saída desta máquina. Descubra o IP com `curl ifconfig.me` |
| VMs sem IP público | VPN Site-to-Site, Azure Bastion, ou colocar o painel numa VM dentro da própria VNet |
| IP de saída dinâmico | Pedir IP fixo ao provedor, ou usar VPN — regra de NSG apontando para IP que muda quebra sozinha |

Além disso, libere a porta do painel (8080 por padrão) no firewall do
Windows para quem for acessar de outra máquina:

```powershell
New-NetFirewallRule -DisplayName "DGT FaceOps" -Direction Inbound `
    -Protocol TCP -LocalPort 8080 -Action Allow
```

### Nos servidores do FindFace

Nada é instalado neles — o painel é agentless. Mas é preciso:

1. **Usuário SSH** com acesso ao grupo `docker` e a `sudo`
2. **Chave PEM** desse usuário (ou a senha)
3. **`sudo` funcionando** — com senha guardada no cofre do painel, ou
   `NOPASSWD` no sudoers

Sem `sudo`, o painel só lê métrica. Backup e reinício de container não
funcionam.

Para `NOPASSWD` restrito ao necessário, em cada servidor:

```bash
sudo tee /etc/sudoers.d/faceops > /dev/null <<'EOF'
# DGT FaceOps — operação do FindFace Multi
azureuser ALL=(root) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose, /usr/bin/tar, /bin/bash
EOF
sudo chmod 0440 /etc/sudoers.d/faceops
sudo visudo -c
```

> Honestidade sobre isso: `NOPASSWD` para `/bin/bash` é equivalente a root
> completo. A restrição acima documenta a intenção e limita acidente, não
> um operador mal-intencionado. Se o modelo de ameaça exigir mais, use senha
> de sudo guardada no cofre em vez de `NOPASSWD`.

## Instalação

1. Instale o **Docker Desktop**, abra uma vez e espere o ícone ficar verde
2. Copie a pasta do projeto para a máquina (ou `git clone`)
3. Clique com o botão direito em `windows\instalar.bat` →
   **Executar como administrador**

O instalador:

- confere Windows, Docker e `docker compose`
- gera a `SECRET_KEY` (64 bytes aleatórios) e a senha do banco
- pergunta a porta e onde guardar os backups
- normaliza o fim de linha dos scripts `.sh` para LF
- constrói as imagens e sobe os containers
- espera o painel responder e cria atalho na área de trabalho

Primeiro acesso: **admin / admin123**. Troque a senha imediatamente.

### Se baixou como ZIP em vez de clonar

O `.gitattributes` garante LF nos scripts `.sh` num `git clone`, mas não
num ZIP baixado. O instalador corrige isso (passo 4/7). Se você rodar o
`deploy.sh` manualmente em vez do instalador, converta antes:

```powershell
Get-ChildItem scripts\*.sh | ForEach-Object {
    $t = [IO.File]::ReadAllText($_.FullName)
    [IO.File]::WriteAllText($_.FullName, $t.Replace("`r`n","`n"))
}
```

Sem isso, o `bash` no container falha com `$'\r': command not found` — um
erro que não diz nada sobre a causa real.

## Uso no dia a dia

| Arquivo | Para quê |
|---|---|
| `windows\iniciar.bat` | sobe o painel e abre o navegador |
| `windows\parar.bat` | para o painel (preserva tudo) |
| `windows\atualizar.bat` | busca a versão nova, reconstrói, reinicia |
| `windows\desinstalar.bat` | remove containers e imagens |

## Subir junto com o Windows

O `restart: unless-stopped` do compose faz os containers voltarem quando o
Docker Desktop sobe. Falta garantir que o **Docker Desktop** suba sozinho:

Docker Desktop → Settings → General → **Start Docker Desktop when you sign
in**.

> Ponto de atenção em Windows Server: isso depende de alguém fazer logon.
> Numa máquina que fica só ligada, sem sessão aberta, o Docker Desktop não
> inicia — e os agendamentos de backup não rodam. Para operação
> desatendida, use `docker` dentro do WSL2 com `systemd` habilitado, ou uma
> VM Linux no Hyper-V. É a razão pela qual a VM Linux dedicada continua
> sendo a opção mais robusta.

## Limitar o consumo do WSL2

Por padrão o WSL2 se serve de metade da RAM da máquina. Para conter, crie
`%USERPROFILE%\.wslconfig`:

```ini
[wsl2]
memory=6GB
processors=4
swap=2GB
```

Depois: `wsl --shutdown` e reabra o Docker Desktop.

## Onde ficam os dados

| Dado | Caminho |
|---|---|
| Backups | pasta escolhida na instalação (padrão `data\backups`) |
| Gravações de terminal | `data\sessions` |
| Banco do painel | volume Docker `dgt-faceops_postgres_data` |
| Configuração e SECRET_KEY | `.env` |

**O `.env` é o arquivo mais importante.** Dele deriva o cofre que cifra as
chaves PEM. Perdê-lo torna todas as credenciais guardadas ilegíveis — os
servidores continuam lá, mas o painel não consegue mais entrar neles, e
será preciso recadastrar cada credencial.

Guarde uma cópia do `.env` fora da máquina, em lugar controlado.

## Verificação pós-instalação

- [ ] `http://localhost:8080` abre a tela de login
- [ ] Login com admin/admin123 funciona, e a faixa de aviso de senha aparece
- [ ] Senha trocada, faixa desapareceu
- [ ] Um servidor cadastrado, com "Ler chave do servidor" retornando fingerprint
- [ ] **Testar conexão** verde, com `sudo: sim`, `docker: sim`, `FindFace: sim`
- [ ] Tela **Recursos** → Atualizar traz RAM, disco e (se houver) GPU
- [ ] Tela **Serviços** lista os containers do FindFace
- [ ] Backup `config` executado com sucesso
- [ ] Backup `essencial` executado, com os bancos listados no log
- [ ] **Recursos → Analisar** mediu o tamanho de `data/`
- [ ] Agendamentos criados com base nesse tamanho
- [ ] InTerminal abre e responde a comando
