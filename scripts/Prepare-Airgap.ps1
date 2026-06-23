[CmdletBinding()]
param(
    [string[]]$Models = @("qwen3:8b", "nomic-embed-text"),
    [string]$OpenWebUIImage = "ghcr.io/open-webui/open-webui:main",
    [string]$OutputDirectory = (Join-Path $PSScriptRoot "..\artifacts"),
    [switch]$IncludePythonWheels,
    [switch]$SkipDownloads
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Command {
    param([Parameter(Mandatory)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory)][string]$Command,
        [Parameter(Mandatory)][string[]]$Arguments
    )

    Write-Host "> $Command $($Arguments -join ' ')" -ForegroundColor Cyan
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

Assert-Command "ollama"
Assert-Command "docker"

$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
$dockerPath = Join-Path $outputPath "docker"
$ollamaPath = Join-Path $outputPath "ollama"
$wheelsPath = Join-Path $outputPath "wheels"

New-Item -ItemType Directory -Force -Path $dockerPath, $ollamaPath | Out-Null

if (-not $SkipDownloads) {
    foreach ($model in $Models) {
        Invoke-Checked "ollama" @("pull", $model)
    }
    Invoke-Checked "docker" @("pull", $OpenWebUIImage)
}

$imageArchive = Join-Path $dockerPath "open-webui.tar"
Invoke-Checked "docker" @("save", "--output", $imageArchive, $OpenWebUIImage)

$modelSource = if ($env:OLLAMA_MODELS) {
    $env:OLLAMA_MODELS
} else {
    Join-Path $env:USERPROFILE ".ollama\models"
}

if (-not (Test-Path -LiteralPath $modelSource -PathType Container)) {
    throw "Ollama model store was not found at '$modelSource'."
}

$modelDestination = Join-Path $ollamaPath "models"
New-Item -ItemType Directory -Force -Path $modelDestination | Out-Null
Copy-Item -LiteralPath (Join-Path $modelSource "blobs") -Destination $modelDestination -Recurse -Force
Copy-Item -LiteralPath (Join-Path $modelSource "manifests") -Destination $modelDestination -Recurse -Force

$Models | Set-Content -LiteralPath (Join-Path $ollamaPath "models.txt") -Encoding utf8
$OpenWebUIImage | Set-Content -LiteralPath (Join-Path $dockerPath "image.txt") -Encoding utf8

if ($IncludePythonWheels) {
    Assert-Command "python"
    New-Item -ItemType Directory -Force -Path $wheelsPath | Out-Null
    $requirements = Join-Path $PSScriptRoot "..\requirements.txt"
    Invoke-Checked "python" @("-m", "pip", "download", "--requirement", $requirements, "--dest", $wheelsPath)
}

$hashes = Get-ChildItem -LiteralPath $outputPath -File -Recurse |
    Where-Object Name -ne "SHA256SUMS.txt" |
    ForEach-Object {
        # Substring keeps this compatible with Windows PowerShell 5.1, whose
        # .NET runtime does not provide Path.GetRelativePath().
        $relativePath = $_.FullName.Substring($outputPath.TrimEnd("\").Length).TrimStart("\")
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relativePath"
    }
$hashes | Set-Content -LiteralPath (Join-Path $outputPath "SHA256SUMS.txt") -Encoding ascii

Write-Host "Air-gap bundle prepared at: $outputPath" -ForegroundColor Green
Write-Host "Copy this directory and the Ollama installer to the offline machine."
