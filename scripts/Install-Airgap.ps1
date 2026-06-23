[CmdletBinding()]
param(
    [string]$BundleDirectory = (Join-Path $PSScriptRoot "..\artifacts"),
    [switch]$InstallPythonWheels
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$bundlePath = [System.IO.Path]::GetFullPath($BundleDirectory)
$imageArchive = Join-Path $bundlePath "docker\open-webui.tar"
$modelSource = Join-Path $bundlePath "ollama\models"
$modelDestination = if ($env:OLLAMA_MODELS) {
    $env:OLLAMA_MODELS
} else {
    Join-Path $env:USERPROFILE ".ollama\models"
}

if (-not (Test-Path -LiteralPath $imageArchive -PathType Leaf)) {
    throw "Docker archive not found: $imageArchive"
}
if (-not (Test-Path -LiteralPath $modelSource -PathType Container)) {
    throw "Ollama model bundle not found: $modelSource"
}

& docker load --input $imageArchive
if ($LASTEXITCODE -ne 0) { throw "docker load failed with exit code $LASTEXITCODE." }

$savedImage = (Get-Content -LiteralPath (Join-Path $bundlePath "docker\image.txt") -Raw).Trim()
$composeImage = "ghcr.io/open-webui/open-webui:main"
if ($savedImage -ne $composeImage) {
    & docker tag $savedImage $composeImage
    if ($LASTEXITCODE -ne 0) { throw "Could not tag the restored Open WebUI image." }
}

New-Item -ItemType Directory -Force -Path $modelDestination | Out-Null
Copy-Item -Path (Join-Path $modelSource "*") -Destination $modelDestination -Recurse -Force

if ($InstallPythonWheels) {
    $requirements = Join-Path $PSScriptRoot "..\requirements.txt"
    $wheels = Join-Path $bundlePath "wheels"
    & python -m pip install --no-index --find-links $wheels --requirement $requirements
    if ($LASTEXITCODE -ne 0) { throw "Offline pip install failed with exit code $LASTEXITCODE." }
}

Write-Host "Offline artifacts installed successfully." -ForegroundColor Green
Write-Host "Restart Ollama, then confirm models with: ollama list"
