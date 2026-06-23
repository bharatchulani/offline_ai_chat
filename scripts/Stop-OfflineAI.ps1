$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

Write-Host "Stopping Open WebUI Docker container..." -ForegroundColor Cyan
docker compose down

Write-Host "Stopping local FastAPI server on port 8000..." -ForegroundColor Cyan
$connections = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    foreach ($processId in ($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped process id: $processId"
    }
}
else {
    Write-Host "No process is listening on port 8000."
}

Write-Host "Done." -ForegroundColor Green
