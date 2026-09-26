[CmdletBinding()]
param(
    [int]$Port = 3210,
    [string]$HostAddress = "127.0.0.1",
    [ValidateSet("LOCAL_ONLY", "LAN_SERVICE")]
    [string]$NetworkMode,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Python = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Repository Python virtual environment is missing" }
$env:PYTHONPATH = Join-Path $RepositoryRoot "apps\api"
$env:LOCAL_DRAMA_HOST = $HostAddress
$env:LOCAL_DRAMA_PORT = [string]$Port
if ($PSBoundParameters.ContainsKey("NetworkMode")) {
    $env:LOCAL_DRAMA_NETWORK_MODE = $NetworkMode
} else {
    Remove-Item env:LOCAL_DRAMA_NETWORK_MODE -ErrorAction SilentlyContinue
}
$Arguments = @("-m", "local_drama.entrypoints.api", "--host", $HostAddress, "--port", [string]$Port)
if ($Reload) { $Arguments += "--reload" }
& $Python @Arguments
$ProcessExitCode = $LASTEXITCODE
if ($null -eq $ProcessExitCode) { $ProcessExitCode = 1 }
exit $ProcessExitCode
