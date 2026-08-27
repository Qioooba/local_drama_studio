[CmdletBinding()]
param(
    [int]$Port = 5173,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Push-Location (Join-Path $RepositoryRoot "apps\web")
try {
    pnpm exec vite --host $HostAddress --port $Port
}
finally {
    Pop-Location
}
