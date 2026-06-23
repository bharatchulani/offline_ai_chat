param(
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Logs = Join-Path $ProjectRoot "logs"
$OutLog = Join-Path $Logs "offline-ai-api.out.log"
$ErrLog = Join-Path $Logs "offline-ai-api.err.log"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-Command {
    param([string]$Name)
    $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

function Test-DockerReady {
    docker version *> $null
    $LASTEXITCODE -eq 0
}

function Start-DockerDesktop {
    $candidates = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    )
    $dockerDesktop = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $dockerDesktop) {
        Write-Warning "Docker Desktop executable was not found. Start Docker Desktop manually, then rerun this launcher."
        return
    }

    Write-Host "Starting Docker Desktop..."
    Start-Process -FilePath $dockerDesktop | Out-Null
}

function Wait-DockerReady {
    param([int]$TimeoutSeconds = 120)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-DockerReady) {
            Write-Host "Docker is ready."
            return $true
        }
        Start-Sleep -Seconds 3
        Write-Host "." -NoNewline
    }
    Write-Host ""
    return $false
}

Set-Location $ProjectRoot
New-Item -ItemType Directory -Force $Logs | Out-Null

Write-Host "Offline AI launcher" -ForegroundColor Green
Write-Host "Project: $ProjectRoot"

if (-not (Test-Command "docker")) {
    throw "Docker CLI was not found. Start Docker Desktop or install Docker Desktop."
}

if (-not (Test-Command "ollama")) {
    Write-Warning "Ollama CLI was not found in PATH. If Open WebUI cannot see models, start Ollama Desktop manually."
}

if (-not (Test-Path $Python)) {
    throw "Python virtual environment not found at $Python. Create it first with: python -m venv .venv"
}

Write-Step "Checking Docker"
if (-not (Test-DockerReady)) {
    Write-Warning "Docker is installed, but the Docker engine is not running yet."
    Start-DockerDesktop
    if (-not (Wait-DockerReady -TimeoutSeconds 150)) {
        throw "Docker Desktop did not become ready. Open Docker Desktop, wait until it says it is running, then rerun Start-OfflineAI.cmd."
    }
}
else {
    Write-Host "Docker is ready."
}

Write-Step "Starting Open WebUI container"
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed. Confirm Docker Desktop is running, then rerun Start-OfflineAI.cmd."
}

Write-Step "Checking Ollama"
try {
    $ollamaResponse = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5
    $modelCount = @($ollamaResponse.models).Count
    Write-Host "Ollama is reachable. Local model count: $modelCount"
}
catch {
    Write-Warning "Ollama is not reachable at http://127.0.0.1:11434."
    Write-Warning "Start Ollama Desktop from the Start menu, then refresh Open WebUI."
}

Write-Step "Starting FastAPI analytics/RAG tool server"
$existingApi = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existingApi) {
    $pids = $existingApi | Select-Object -ExpandProperty OwningProcess -Unique
    Write-Host "Port 8000 is already listening. Existing process id(s): $($pids -join ', ')"
}
else {
    $process = Start-Process `
        -FilePath $Python `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--env-file", ".env", "--host", "0.0.0.0", "--port", "8000") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -PassThru
    Write-Host "Started API process id: $($process.Id)"
    Start-Sleep -Seconds 4
}

Write-Step "Health checks"
try {
    $apiHealth = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 10
    Write-Host "FastAPI: $($apiHealth.status), chat model: $($apiHealth.chat_model), embedding model: $($apiHealth.embedding_model)"
}
catch {
    Write-Warning "FastAPI health check failed. Check logs:"
    Write-Warning "  $OutLog"
    Write-Warning "  $ErrLog"
}

try {
    $webui = Invoke-WebRequest -Uri "http://127.0.0.1:3000" -UseBasicParsing -TimeoutSec 10
    Write-Host "Open WebUI: HTTP $($webui.StatusCode)"
}
catch {
    Write-Warning "Open WebUI did not respond yet. Docker may still be starting it. Try http://localhost:3000 in a minute."
}

Write-Host ""
Write-Host "Ready URLs" -ForegroundColor Green
Write-Host "  Open WebUI:      http://localhost:3000"
Write-Host "  API docs:        http://127.0.0.1:8000/docs"
Write-Host "  OpenAPI for UI:  http://host.docker.internal:8000/openapi.json"
Write-Host ""
Write-Host "To stop later:"
Write-Host "  .\scripts\Stop-OfflineAI.ps1"

if ($OpenBrowser) {
    Start-Process "http://localhost:3000"
}
