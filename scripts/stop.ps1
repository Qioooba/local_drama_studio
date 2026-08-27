[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$HostBinary = Join-Path $RepositoryRoot "work\runtime-host-dev\local-drama-host.exe"
if (-not (Test-Path -LiteralPath $HostBinary -PathType Leaf)) {
    throw "Development Runtime Host is not built; no Host-owned process can be stopped safely"
}
$env:LOCAL_DRAMA_INSTALL_ROOT = $RepositoryRoot
$env:LOCAL_DRAMA_INSTANCE_ROOT = $RepositoryRoot
$env:LOCAL_DRAMA_PYTHON = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
& $HostBinary stop
if ($LASTEXITCODE -ne 0) { throw "Runtime Host stop request failed" }
Write-Output "Runtime Host stop requested"
