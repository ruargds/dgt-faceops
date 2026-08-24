# ===========================================================================
# DGT FaceOps — instalação no Windows
#
# Sobe o painel inteiro em Docker Desktop (backend WSL2). Não é preciso
# criar VM Linux à mão: o WSL2 já é a VM.
#
# Não chame este arquivo direto — use instalar.bat (ele trata a política
# de execução do PowerShell).
# ===========================================================================
$ErrorActionPreference = "Stop"

$Raiz = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Raiz

function Titulo($texto) {
    Write-Host ""
    Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  $texto" -ForegroundColor Cyan
    Write-Host "════════════════════════════════════════════════════" -ForegroundColor Cyan
}
function Passo($n, $texto) { Write-Host "[$n] $texto" -ForegroundColor White }
function Ok($texto)    { Write-Host "    OK  $texto" -ForegroundColor Green }
function Aviso($texto) { Write-Host "    !   $texto" -ForegroundColor Yellow }
function Falha($texto) {
    Write-Host "    X   $texto" -ForegroundColor Red
    Write-Host ""
    Read-Host "Pressione Enter para sair"
    exit 1
}

Titulo "DGT FaceOps — instalação"

# ── 1. Pré-requisitos ──────────────────────────────────────────────────
Passo "1/7" "Verificando pré-requisitos..."

$versaoSO = [System.Environment]::OSVersion.Version
if ($versaoSO.Major -lt 10) {
    Falha "Windows 10 / Server 2016 ou superior é necessário (encontrado $versaoSO)."
}
Ok "Windows $versaoSO"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "    Docker Desktop não encontrado." -ForegroundColor Red
    Write-Host ""
    Write-Host "    Instale e rode uma vez antes de continuar:"
    Write-Host "      https://www.docker.com/products/docker-desktop/"
    Write-Host ""
    Write-Host "    No Windows Server, habilite o WSL2 antes:"
    Write-Host "      wsl --install"
    Write-Host "      (reiniciar)"
    Write-Host ""
    Falha "Instale o Docker Desktop e rode este instalador de novo."
}
Ok "Docker encontrado"

# `docker info` falha se o serviço estiver parado — checar o cliente não basta
try {
    docker info --format '{{.ServerVersion}}' 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "engine parado" }
    $versaoDocker = (docker info --format '{{.ServerVersion}}')
    Ok "Docker Engine $versaoDocker respondendo"
} catch {
    Falha "O Docker Desktop está instalado mas não está rodando. Abra o Docker Desktop, espere ficar verde e rode de novo."
}

# Plugin compose v2
docker compose version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Falha "'docker compose' não disponível. Atualize o Docker Desktop."
}
Ok "docker compose disponível"

# ── 2. Configuração ────────────────────────────────────────────────────
Passo "2/7" "Configurando o ambiente..."

if (Test-Path ".env") {
    Aviso ".env já existe — vou manter o que está lá."
    $reconfig = Read-Host "    Reconfigurar do zero? Isto TROCA a SECRET_KEY e torna as credenciais guardadas ilegíveis (s/N)"
    if ($reconfig -eq "s") {
        Copy-Item ".env" ".env.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
        Remove-Item ".env"
        Ok "Backup do .env anterior guardado"
    }
}

if (-not (Test-Path ".env")) {
    if (-not (Test-Path ".env.example")) { Falha ".env.example não encontrado. Repositório incompleto?" }

    # SECRET_KEY: 64 bytes aleatórios em base64 url-safe. Desta chave deriva
    # o cofre Fernet que guarda as chaves PEM — não pode ser previsível.
    $bytes = New-Object byte[] 64
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $chave = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')

    Write-Host ""
    $porta = Read-Host "    Porta do painel [8080]"
    if ([string]::IsNullOrWhiteSpace($porta)) { $porta = "8080" }

    Write-Host ""
    Write-Host "    Onde guardar os artefatos de backup?" -ForegroundColor Yellow
    Write-Host "    Use um disco com espaço: o perfil Completo gera centenas de GB."
    $discoBackup = Read-Host "    Caminho [$Raiz\data\backups]"
    if ([string]::IsNullOrWhiteSpace($discoBackup)) { $discoBackup = "$Raiz\data\backups" }

    $senhaBanco = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })

    $conteudo = Get-Content ".env.example" -Raw
    $conteudo = $conteudo -replace 'SECRET_KEY=troque-esta-chave', "SECRET_KEY=$chave"
    $conteudo = $conteudo -replace 'POSTGRES_PASSWORD=troque-esta-senha', "POSTGRES_PASSWORD=$senhaBanco"
    $conteudo = $conteudo -replace 'PORTA_HTTP=8080', "PORTA_HTTP=$porta"
    Set-Content -Path ".env" -Value $conteudo -NoNewline -Encoding UTF8

    Ok "SECRET_KEY gerada (64 bytes aleatórios)"
    Ok "Senha do banco gerada"
    Ok "Painel na porta $porta"

    if ($discoBackup -ne "$Raiz\data\backups") {
        # Aponta o bind mount do compose para o disco escolhido
        $caminhoDocker = $discoBackup -replace '\\','/'
        $compose = Get-Content "docker-compose.yml" -Raw
        $compose = $compose -replace '- \./data/backups:/data/backups', "- ${caminhoDocker}:/data/backups"
        Set-Content -Path "docker-compose.yml" -Value $compose -NoNewline
        Ok "Backups vão para $discoBackup"
    }
}

# ── 3. Diretórios ──────────────────────────────────────────────────────
Passo "3/7" "Criando diretórios de dados..."
foreach ($d in @("data\backups", "data\sessions", "rclone")) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}
if (-not (Test-Path "rclone\rclone.conf")) { New-Item -ItemType File -Path "rclone\rclone.conf" | Out-Null }
Ok "data\backups, data\sessions, rclone"

# ── 4. Fim de linha dos scripts ────────────────────────────────────────
# O ffmulti-backup.sh roda dentro de container Linux. Se o Git converteu
# para CRLF no checkout, o bash morre com "$'\r': command not found" — erro
# que não aponta para a causa. .gitattributes previne, mas ZIP baixado não.
Passo "4/7" "Normalizando fim de linha dos scripts..."
$corrigidos = 0
Get-ChildItem -Path "scripts" -Filter "*.sh" -Recurse | ForEach-Object {
    $texto = [System.IO.File]::ReadAllText($_.FullName)
    if ($texto.Contains("`r`n")) {
        [System.IO.File]::WriteAllText($_.FullName, $texto.Replace("`r`n", "`n"))
        $corrigidos++
    }
}
if ($corrigidos -gt 0) { Ok "$corrigidos script(s) convertido(s) para LF" } else { Ok "já estavam em LF" }

# ── 5. Build ───────────────────────────────────────────────────────────
Passo "5/7" "Construindo as imagens (primeira vez leva alguns minutos)..."
docker compose build
if ($LASTEXITCODE -ne 0) { Falha "O build falhou. Veja as mensagens acima." }
Ok "Imagens construídas"

# ── 6. Subir ───────────────────────────────────────────────────────────
Passo "6/7" "Subindo os serviços..."
docker compose up -d --remove-orphans
if ($LASTEXITCODE -ne 0) { Falha "Falha ao subir os containers." }

$porta = (Select-String -Path ".env" -Pattern '^PORTA_HTTP=(.*)$').Matches.Groups[1].Value
if ([string]::IsNullOrWhiteSpace($porta)) { $porta = "8080" }

Passo "7/7" "Aguardando o painel responder..."
$pronto = $false
for ($i = 1; $i -le 45; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$porta/api/saude" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { $pronto = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
    if ($i % 5 -eq 0) { Write-Host "    ... $($i*2)s" -ForegroundColor DarkGray }
}

if (-not $pronto) {
    Aviso "O painel não respondeu em 90s."
    Write-Host "    Veja o log:  docker compose logs --tail 60 backend" -ForegroundColor Yellow
    Read-Host "Pressione Enter para sair"
    exit 1
}
Ok "Painel respondendo"

# ── Atalho na área de trabalho ─────────────────────────────────────────
try {
    $shell = New-Object -ComObject WScript.Shell
    $atalho = $shell.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\DGT FaceOps.url")
    $atalho.TargetPath = "http://localhost:$porta"
    $atalho.Save()
    Ok "Atalho criado na área de trabalho"
} catch {
    Aviso "Não consegui criar o atalho (não é problema)."
}

Titulo "Instalado"
Write-Host "  Endereço.........: " -NoNewline; Write-Host "http://localhost:$porta" -ForegroundColor Cyan
Write-Host "  Primeiro acesso..: " -NoNewline; Write-Host "admin / admin123" -ForegroundColor Yellow
Write-Host ""
Write-Host "  TROQUE A SENHA no primeiro acesso. Enquanto ela for a de" -ForegroundColor Yellow
Write-Host "  fábrica, quem alcançar esta máquina na rede entra no painel" -ForegroundColor Yellow
Write-Host "  e, por consequência, nos servidores do FindFace." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Próximo passo: cadastrar os servidores em Servidores >"
Write-Host "  Cadastrar servidor. Tenha em mãos IP, usuário SSH e a chave PEM."
Write-Host ""
Write-Host "  Atalhos:  iniciar.bat | parar.bat | atualizar.bat | desinstalar.bat"
Write-Host ""
Read-Host "Pressione Enter para fechar"
