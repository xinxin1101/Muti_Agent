[CmdletBinding()]
param(
    [switch]$KeepInfra
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateFile = Join-Path $RepoRoot ".devflow\local-dev-processes.json"

function Find-Docker {
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $fallback = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $fallback) {
        return $fallback
    }
    throw "Docker was not found."
}

function Stop-RecordedService {
    param([Parameter(Mandatory = $true)]$Service)

    $process = Get-Process -Id $Service.pid -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Host "$($Service.name): already stopped."
        return
    }

    $actualStart = $process.StartTime.ToUniversalTime()
    $expectedStart = if ($Service.started_at_utc -is [DateTime]) {
        $Service.started_at_utc.ToUniversalTime()
    }
    else {
        [DateTimeOffset]::Parse([string]$Service.started_at_utc).UtcDateTime
    }
    # Process.StartTime and JSON serialization have different clock precision on
    # Windows. Compare with a narrow tolerance so an active DevFlow terminal is
    # not mistaken for a reused PID, while still protecting an unrelated process.
    if ([Math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 2) {
        Write-Warning "$($Service.name): PID $($Service.pid) has been reused; it will not be stopped."
        return
    }

    & taskkill.exe /PID $Service.pid /T /F | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$($Service.name): stopped."
    }
    else {
        Write-Warning "$($Service.name): could not be stopped automatically."
    }
}

if (Test-Path -LiteralPath $StateFile) {
    $services = Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json
    foreach ($service in @($services)) {
        Stop-RecordedService $service
    }
    Remove-Item -LiteralPath $StateFile -Force
}
else {
    Write-Host "No local DevFlow service-process record was found."
}

if (-not $KeepInfra) {
    $docker = Find-Docker
    & $docker compose -f (Join-Path $RepoRoot "compose.dev.yml") down
    if ($LASTEXITCODE -ne 0) { throw "Could not stop PostgreSQL and Redis." }
    Write-Host "PostgreSQL and Redis are stopped; their named volume is preserved."
}
else {
    Write-Host "PostgreSQL and Redis were left running."
}
