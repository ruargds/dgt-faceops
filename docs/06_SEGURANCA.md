# Segurança

## Modelo de ameaça

O que este painel protege contra:

- **Credencial em repouso.** Chave PEM e senhas cifradas; nunca voltam pela API.
- **Impostor na rede.** Identidade do servidor fixada antes de credencial trafegar.
- **Erro operacional.** Cercas, dupla confirmação e aceite de janela.
- **Falta de rastro.** Toda ação registrada; toda sessão de terminal gravada.

O que **não** protege contra, e é importante dizer:

- **Administrador do painel mal-intencionado.** Quem tem `terminal.use` +
  `terminal.sudo` tem root nos servidores, por definição. A gravação da
  sessão é detecção, não prevenção.
- **Comprometimento da máquina do painel.** Quem tem root nela lê o `.env`,
  deriva a chave Fernet e decifra o cofre. O painel é um alvo de alto valor
  — trate-o como tal.
- **Exposição direta na internet.** Não foi endurecido para isso. Se
  publicar, ponha TLS e autenticação de borda na frente.

## Cofre de credenciais

`core/vault.py` — Fernet (AES-128-CBC + HMAC-SHA256), chave derivada por
SHA-256 da `SECRET_KEY`.

Guarda: chave PEM, senha da chave, senha SSH, senha de sudo, senha/token
da API do Face Detect e o **token do bot do Telegram**. Colunas `*_enc`.

O token do bot merece nota: quem o tem manda mensagem como o bot. Além de
cifrado, ele é removido de log e de mensagem de erro — a URL do Telegram
carrega o token no caminho, então um traceback de rede o levaria junto
para dentro de um chamado ou de um anexo de e-mail
(`telegram_service._limpar`). Há cenário de teste que falha se ele
aparecer numa resposta de API ou num erro.

**Nenhum schema de saída expõe essas colunas.** A tela confirma o que está
guardado por fingerprint (SHA-256 truncado em 16 caracteres) — suficiente
para saber *qual* chave está lá, inútil para reconstruí-la.

O valor em claro existe só em memória, no momento da conexão SSH.

### A SECRET_KEY

Consequências de trocá-la: **todas as credenciais guardadas ficam
ilegíveis.** Os servidores continuam lá, o painel não consegue mais entrar
neles, e é preciso recadastrar cada credencial.

Por isso `decrypt_secret` levanta erro explícito em vez de devolver lixo:

```
Segredo ilegível: a SECRET_KEY mudou desde que foi gravado.
Recadastre a credencial do host.
```

Falha clara vale mais que horas caçando problema de rede inexistente.

O `deploy.sh` recusa subir com a `SECRET_KEY` de exemplo.

**Guarde uma cópia do `.env` fora da máquina, em lugar controlado.**

## Pinagem de chave de host

O ponto de segurança mais importante do projeto.

### O problema

Autenticação SSH por senha envia a senha **durante o handshake**. Se você
conecta com `known_hosts=None` (aceitar qualquer chave) e só depois compara
fingerprint, um atacante no caminho da rede já recebeu a senha de sudo.

### A solução

`asyncssh.get_server_host_key()` lê a chave pública do servidor **sem
autenticar**. É um passo separado e explícito na UI ("Ler chave do
servidor"), obrigatório antes de cadastrar credencial.

A chave é guardada em `hosts.host_key_pub` e toda conexão posterior é fixada
nela: `known_hosts=([chave], [], [])`.

### Quando não confere

```
HostKeyMismatch: a chave de 'vm-appserver' (10.0.1.10) NAO confere com a
cadastrada. Conexao abortada sem enviar credenciais. Se o servidor foi
reinstalado, refaca a varredura de chave; caso contrario, trate como
incidente de seguranca.
```

A conexão é abortada **antes** de qualquer credencial sair. Duas causas
possíveis:

1. **Legítima** — servidor reinstalado, chave nova. Edite o host e refaça a
   leitura da chave.
2. **Ataque** — alguém no caminho da rede. Investigue antes de refazer.

O painel não escolhe por você, e não deve.

Trocar o endereço de um host também dispara nova leitura: a identidade
fixada vale para o par (endereço, porta), não para o nome.

## Senha de sudo por stdin

```python
alvo = f"sudo -S -p '' -- {command}"
entrada = senha + "\n"
```

`-S` lê a senha da entrada padrão. Ela nunca aparece na linha de comando, e
portanto nunca no `ps` de quem estiver logado no servidor.

Vale também para o script de backup: `sudo -S -p '' bash -s`, com a senha na
primeira linha e o script no resto.

Sem senha guardada, cai em `sudo -n` (assume `NOPASSWD`).

## Auditoria

`audit_logs` registra toda ação que muda estado. Campos: quando, usuário,
IP, ação, alvo, nível, sucesso, detalhe.

### Higienização do detalhe

`audit_service._limpar()` substitui por `<omitido>` qualquer chave cujo nome
contenha:

```
password · senha · ssh_key · ssh_password · sudo_password
ssh_key_passphrase · secret · token · access_token · pem
```

Recursivo em dicionário aninhado. Strings acima de 2000 caracteres são
truncadas.

Motivo: auditoria é lida por mais gente do que o cofre. Um segredo que
escorregue para o detalhe estaria visível para todo mundo com `audit.view`.

### Níveis

| Nível | Quando |
|---|---|
| `info` | ação normal bem-sucedida |
| `warning` | qualquer falha |
| `critical` | ação em `DESTRUCTIVE_PERMISSIONS` |

Login falho é registrado com o usuário tentado e o motivo — sem a senha.

### IP real

O painel roda atrás do nginx, então o socket sempre mostraria o IP do proxy.
`client_ip()` lê `X-Forwarded-For` (primeiro valor) com fallback para o
socket. Sem isso o log de auditoria registraria o IP do container em todas
as linhas — inútil.

## Terminal — ticket de uso único

Detalhado em [07_INTERMINAL](07_INTERMINAL.md). Em resumo: o JWT não vai na
URL do WebSocket. Um ticket de 32 bytes aleatórios, válido 30 segundos, de
uso único e amarrado a um host específico é trocado pela conexão.

Motivo: token em query string vaza no log de acesso do nginx, no histórico
do navegador e em qualquer proxy no caminho.

## Cercas de escopo

### Container de fora do projeto

Antes de reiniciar, `_garantir_do_projeto()` confere o rótulo
`com.docker.compose.project` do container alvo contra o projeto do Face Detect
daquele host. Diferente → recusa com mensagem explicando.

Sem essa cerca, `POST /services/{id}/restart` com nome arbitrário derrubaria
qualquer container do servidor.

O nome do serviço passa antes por allowlist (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`),
rejeitando antes de chegar perto do shell.

### Travessia de diretório

`StorageService.caminho_artefato()` rejeita nome com `/`, `\` ou iniciando
com `.`, e confere o caminho resolvido contra o diretório base. Mesma
checagem na gravação de terminal.

## Autenticação

- Senha com bcrypt (passlib)
- JWT HS256, validade padrão de 8h (`ACCESS_TOKEN_EXPIRE_MINUTES`)
- Token em `localStorage`

> Sobre `localStorage`: cookie httpOnly resistiria melhor a XSS. A escolha
> aqui é consciente — o painel roda em rede interna e o InTerminal precisa
> do token em JavaScript para pedir o ticket. Se o painel for exposto,
> reavalie.

Login com usuário inexistente e com senha errada devolvem a **mesma
mensagem**. Diferenciar entregaria a lista de usuários válidos a quem tenta.

## Saídas de rede do painel

O painel é agentless e conversa para fora em três direções, e só nestas:

| Destino | Porta | Quando | Por quê |
|---|---|---|---|
| Servidores do Face Detect | 22 | coleta, backup, terminal | SSH — é a única via de leitura e ação |
| API do Face Detect (opcional) | 443/80 | licença, câmeras, retenção | quando URL e credencial estão cadastradas |
| `api.telegram.org` | 443 | só quando há evento a avisar | envio de aviso ([28](28_AVISOS_TELEGRAM.md)) |
| Destinos de backup | conforme o destino | fim de cada execução | Azure Blob / rclone, quando configurados |

Não há escuta de entrada além da porta do próprio painel. O bot do
Telegram **não** faz long-polling: só há chamada de saída, e só quando
algo cai — não existe processo aguardando comando de fora.

## Superfície de ataque

| Exposição | Risco | Mitigação |
|---|---|---|
| Porta do painel (8080) | acesso à interface | rede interna; firewall; senha trocada |
| WebSocket do terminal | sessão de shell | ticket de uso único; permissão; gravação |
| `.env` na máquina do painel | cofre inteiro | permissão de arquivo; disco cifrado; cópia externa |
| Volume do banco | cadastro e histórico (segredos cifrados) | acesso à máquina |
| `data/backups` | dados do Face Detect em claro | permissão; considere cifrar o disco |
| Chave SSH no cofre | root nos 4 servidores | Fernet; nunca sai pela API |

**Os artefatos de backup contêm dados do Face Detect sem cifra adicional** —
dossiês, vetores faciais, cadastros. Trate o disco de backup com o mesmo
cuidado que o servidor de origem. Considere disco cifrado, e no Azure use
container privado com política de acesso restrita.

## Endurecimento recomendado

Não vem por padrão, mas vale para produção:

```bash
# TLS na frente do painel (nginx externo, Caddy ou Cloudflare Tunnel)
# Sem TLS, a senha do painel trafega em claro na rede interna.

# Firewall: só quem precisa
sudo ufw allow from 10.0.0.0/8 to any port 8080

# Permissão do .env
chmod 600 .env

# Sessão mais curta, se a rotatividade for alta
ACCESS_TOKEN_EXPIRE_MINUTES=120
```

## Rotação de credencial

Trocar a chave SSH de um servidor:

1. Gere o par novo e publique a pública no servidor
2. **Servidores → Editar** → cole a privada nova → Salvar
3. **Testar conexão** — precisa vir verde
4. Só então remova a chave antiga do `authorized_keys` do servidor

O painel derruba a conexão em cache ao salvar, então a próxima operação já
usa a credencial nova.

Deixar a antiga até o teste passar evita perder acesso por chave nova
errada.
