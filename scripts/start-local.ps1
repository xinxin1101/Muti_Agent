[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$SkipVerifier
)

$StartScript = Join-Path $PSScriptRoot "start-dev.ps1"
$StartParameters = @{
    AllowMissingProviderKey = $true
}

if ($SkipInstall) {
    $StartParameters.SkipInstall = $true
}
if ($SkipVerifier) {
    # UI-only diagnostics can opt out explicitly. A normal local start builds the
    # trusted verifier images so task runs do not fail only after code generation.
    $StartParameters.SkipVerifier = $true
}

& $StartScript @StartParameters
