[CmdletBinding()]
param(
    [switch]$SkipVerifier,
    [switch]$SkipInstall,
    [switch]$AllowMissingProviderKey
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $RepoRoot "backend"
$FrontendDir = Join-Path $RepoRoot "frontend"
$EnvFile = Join-Path $RepoRoot ".env"
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$VenvActivate = Join-Path $RepoRoot ".venv\Scripts\Activate.ps1"
$RuntimeStateDir = Join-Path $RepoRoot ".devflow"
$ServerPidsFile = Join-Path $RuntimeStateDir "local-dev-processes.json"
$RuntimeIdentityFile = Join-Path $RuntimeStateDir "runtime-identity.json"
$StopScript = Join-Path $PSScriptRoot "stop-dev.ps1"

function Require-File {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Message)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw $Message
    }
}

function Find-Command {
    param([Parameter(Mandatory = $true)][string]$Name, [string]$Fallback)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    if ($Fallback -and (Test-Path -LiteralPath $Fallback)) {
        return $Fallback
    }
    throw "Required command '$Name' was not found. Install it or add it to PATH."
}

function Invoke-InDirectory {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][scriptblock]$Script)

    Push-Location $Path
    try {
        & $Script
    }
    finally {
        Pop-Location
    }
}

function Get-EnvFileValue {
    param([Parameter(Mandatory = $true)][string]$Name)

    $match = Select-String -LiteralPath $EnvFile -Pattern "^\s*$([regex]::Escape($Name))\s*=\s*(.+?)\s*$" | Select-Object -First 1
    if (-not $match) {
        return $null
    }
    return $match.Matches[0].Groups[1].Value.Trim()
}

function Start-ServiceTerminal {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][string]$RuntimeFingerprint
    )

    $escapedRoot = $RepoRoot.Replace("'", "''")
    $escapedActivate = $VenvActivate.Replace("'", "''")
    $terminalCommand = "`$Host.UI.RawUI.WindowTitle = '$Title'; `$env:DEVFLOW_RUNTIME_FINGERPRINT = '$RuntimeFingerprint'; Set-Location -LiteralPath '$escapedRoot'; & '$escapedActivate'; $Command"
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoExit", "-Command", $terminalCommand) -PassThru
    return [PSCustomObject]@{
        name = $Title
        pid = $process.Id
        started_at_utc = $process.StartTime.ToUniversalTime().ToString("o")
    }
}

Require-File $EnvFile "Missing .env. Create it first with: Copy-Item .env.example .env"
if (-not (Select-String -LiteralPath $EnvFile -Pattern '^\s*SILICONFLOW_API_KEY\s*=\s*\S+' -Quiet)) {
    if ($AllowMissingProviderKey) {
        Write-Warning "SILICONFLOW_API_KEY is not configured. The local UI and API can start, but Agent Runs and /readyz will remain unavailable."
    }
    else {
        throw "SILICONFLOW_API_KEY is missing in .env. Add it before starting DevFlow."
    }
}

$proxyUrl = Get-EnvFileValue "DEVFLOW_PROXY_URL"
if ($proxyUrl) {
    $proxyUri = $null
    if (-not [Uri]::TryCreate($proxyUrl, [UriKind]::Absolute, [ref]$proxyUri) -or $proxyUri.Scheme -notin @("http", "https")) {
        throw "DEVFLOW_PROXY_URL must be an absolute http:// or https:// proxy URL."
    }
    $env:HTTP_PROXY = $proxyUri.AbsoluteUri.TrimEnd('/')
    $env:HTTPS_PROXY = $proxyUri.AbsoluteUri.TrimEnd('/')
    $bypass = @("127.0.0.1", "localhost")
    if ($env:NO_PROXY) {
        $bypass += $env:NO_PROXY -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ }
    }
    $env:NO_PROXY = ($bypass | Select-Object -Unique) -join ','
    Write-Host "DevFlow API and Worker will use the configured local proxy."
}

$docker = Find-Command "docker" "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
$node = Find-Command "node" "D:\Node\node.exe"
$npm = Find-Command "npm" "D:\Node\npm.cmd"
$python = Find-Command "python"

# Keep child Python/Node processes compatible with installations discovered via
# the fallback paths above, even when the caller's PATH has not been refreshed.
foreach ($commandPath in @($docker, $node, $npm)) {
    $commandDirectory = Split-Path -Parent $commandPath
    if (($env:Path -split ';') -notcontains $commandDirectory) {
        $env:Path = "$commandDirectory;$env:Path"
    }
}

$dockerServerVersion = & $docker version --format '{{.Server.Version}}' 2>$null
if ($LASTEXITCODE -ne 0 -or -not $dockerServerVersion) {
    throw "Docker Desktop is not ready. Start Docker Desktop, wait for it to report Running, then retry."
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $python -m venv (Join-Path $RepoRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Could not create the repository Python environment." }
}

# Never let a newly edited source tree run beside terminals launched by a previous
# source version. The stop script only targets PIDs it recorded and leaves Docker
# infrastructure running, so persisted PostgreSQL state remains untouched.
if (Test-Path -LiteralPath $ServerPidsFile) {
    & $StopScript -KeepInfra
}

$RuntimeFingerprint = Invoke-InDirectory $BackendDir {
    & $VenvPython -c "from app.core.runtime_identity import current_runtime_fingerprint; print(current_runtime_fingerprint())"
}
if ($LASTEXITCODE -ne 0 -or -not ($RuntimeFingerprint -match '^[0-9a-f]{64}$')) {
    throw "Could not calculate the DevFlow runtime source fingerprint."
}

# A stable local key lets PostgreSQL keep project-specific GitHub publication credentials encrypted
# across API restarts. It is generated only once, stored in ignored .env, and never printed.
if (-not (Get-EnvFileValue "DEVFLOW_SECRETS_ENCRYPTION_KEY")) {
    $generatedSecretKey = & $VenvPython -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode('ascii'))"
    if ($LASTEXITCODE -ne 0 -or -not $generatedSecretKey) {
        throw "Could not generate DEVFLOW_SECRETS_ENCRYPTION_KEY for encrypted local credentials."
    }
    Add-Content -LiteralPath $EnvFile -Value "`nDEVFLOW_SECRETS_ENCRYPTION_KEY=$generatedSecretKey"
    Write-Host "Generated the local encryption key for persistent project credentials."
}

if (-not $SkipInstall) {
    Invoke-InDirectory $RepoRoot {
        & $VenvPython -m pip install -r backend\requirements-dev.lock
        if ($LASTEXITCODE -ne 0) { throw "Could not install locked backend dependencies." }
        & $VenvPython -m pip install --no-deps -e backend
        if ($LASTEXITCODE -ne 0) { throw "Could not install the DevFlow backend package." }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "node_modules"))) {
        Invoke-InDirectory $FrontendDir {
            & $npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "Could not install frontend dependencies." }
        }
    }
}

& $docker compose -f (Join-Path $RepoRoot "compose.dev.yml") up -d --wait
if ($LASTEXITCODE -ne 0) { throw "Could not start PostgreSQL and Redis." }
& $docker compose -f (Join-Path $RepoRoot "compose.dev.yml") exec -T redis redis-cli ping | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Redis did not pass its startup PING check." }
Invoke-InDirectory $BackendDir {
    & $VenvPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }
}

if (-not $SkipVerifier) {
    # `docker image inspect` exits with 1 for an absent image. With PowerShell's
    # fail-fast preference that expected result is promoted to a terminating error,
    # so query the image list instead: no match is a successful empty result.
    $pythonImageExists = [bool](& $docker image ls --filter "reference=devflow-verifier:py311" --format "{{.Repository}}:{{.Tag}}")
    $nodeImageExists = [bool](& $docker image ls --filter "reference=devflow-verifier:node24" --format "{{.Repository}}:{{.Tag}}")
    if (-not ($pythonImageExists -and $nodeImageExists)) {
        Invoke-InDirectory $RepoRoot {
            & $VenvPython scripts\build_verification_bases.py
            if ($LASTEXITCODE -ne 0) {
                throw @"
Could not build trusted verification images. Docker Desktop could not pull a required image from Docker Hub.
Check Docker Desktop Settings > Resources > Proxies and Network, apply any required proxy/DNS settings, restart Docker Desktop, then verify with:
  docker pull python:3.11.15-slim-bookworm
For UI-only troubleshooting before verifier images are available, run:
  .\scripts\start-dev.ps1 -SkipVerifier
Runs will not pass readiness or deterministic verification until both verifier images have been built.
"@
            }
        }
    }
}

$services = @(
    Start-ServiceTerminal "DevFlow API" "Set-Location -LiteralPath '$BackendDir'; & '$VenvPython' -m app.api.main" $RuntimeFingerprint
    Start-ServiceTerminal "DevFlow Worker" "Set-Location -LiteralPath '$BackendDir'; & '$VenvPython' -m dramatiq app.workers.tasks" $RuntimeFingerprint
    Start-ServiceTerminal "DevFlow Frontend" "Set-Location -LiteralPath '$FrontendDir'; & '$npm' run dev" $RuntimeFingerprint
)
New-Item -ItemType Directory -Force -Path $RuntimeStateDir | Out-Null
$services | ConvertTo-Json | Set-Content -LiteralPath $ServerPidsFile -Encoding utf8
@{
    runtime_fingerprint = $RuntimeFingerprint
    started_at_utc = [DateTime]::UtcNow.ToString("o")
} | ConvertTo-Json | Set-Content -LiteralPath $RuntimeIdentityFile -Encoding utf8

Write-Host "DevFlow is starting in three new terminals."
Write-Host "Frontend: http://127.0.0.1:5173"
Write-Host "API:      http://127.0.0.1:8000/healthz"
Write-Host "Readiness: http://127.0.0.1:8000/readyz"
Write-Host "Runtime source fingerprint: $RuntimeFingerprint"
