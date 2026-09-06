[CmdletBinding()]
param(
    [int]$Port = 5173,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$ProcessExitCode = 1
Push-Location (Join-Path $RepositoryRoot "apps\web")
try {
    pnpm exec vite --host $HostAddress --port $Port
    $ProcessExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
if ($null -eq $ProcessExitCode) { $ProcessExitCode = 1 }
exit $ProcessExitCode
