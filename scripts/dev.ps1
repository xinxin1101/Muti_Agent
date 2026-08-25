param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("check", "infra", "down", "migrate", "verifier", "api", "worker", "frontend")]
    [string]$Command
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $RepoRoot "backend"
$FrontendDir = Join-Path $RepoRoot "frontend"
$ComposeFile = Join-Path $RepoRoot "compose.dev.yml"
$EnvFile = Join-Path $RepoRoot ".env"

function Invoke-InDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][scriptblock]$Script
    )

    Push-Location $Path
    try {
        & $Script
    }
    finally {
        Pop-Location
    }
}

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found on PATH."
    }
}

switch ($Command) {
    "check" {
        Require-Command "python"
        Require-Command "node"
        Require-Command "npm"
        Require-Command "git"
        Require-Command "docker"

        python --version
        node --version
        npm --version
        git --version
        docker --version
        docker compose version
        docker compose -f $ComposeFile config --quiet

        if (-not (Test-Path $EnvFile)) {
            Write-Warning "Repository-root .env is missing. Run: Copy-Item .env.example .env"
        }
    }
    "infra" {
        Require-Command "docker"
        docker compose -f $ComposeFile up -d --wait
    }
    "down" {
        Require-Command "docker"
        docker compose -f $ComposeFile down
    }
    "migrate" {
        Invoke-InDirectory $BackendDir { alembic upgrade head }
    }
    "verifier" {
        Require-Command "docker"
        Invoke-InDirectory $RepoRoot { python scripts/build_verification_bases.py }
    }
    "api" {
        Invoke-InDirectory $BackendDir { devflow-api }
    }
    "worker" {
        Invoke-InDirectory $BackendDir { dramatiq app.workers.tasks }
    }
    "frontend" {
        Invoke-InDirectory $FrontendDir {
            npm ci --no-audit --no-fund
            npm run dev
        }
    }
}
