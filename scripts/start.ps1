[CmdletBinding()]
param(
    [int]$Port = 3210,
    [string]$HostAddress = "127.0.0.1",
    [ValidateSet("LOCAL_ONLY", "LAN_SERVICE")]
    [string]$NetworkMode = "LOCAL_ONLY"
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $RepositoryRoot "work\runtime-host-dev"
$HostBinary = Join-Path $BuildRoot "local-drama-host.exe"
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null

Write-Warning "scripts/start.ps1 is a development compatibility entrypoint; packaged deployments use LocalDramaStudio Host directly."
Push-Location (Join-Path $RepositoryRoot "cmd\runtime-host")
try {
    go build -trimpath -ldflags "-X main.hostVersion=development" -o $HostBinary .
    if ($LASTEXITCODE -ne 0) { throw "Runtime Host build failed" }
}
finally {
    Pop-Location
}

$env:LOCAL_DRAMA_INSTALL_ROOT = $RepositoryRoot
$env:LOCAL_DRAMA_INSTANCE_ROOT = $RepositoryRoot
$env:LOCAL_DRAMA_PYTHON = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
$env:LOCAL_DRAMA_HOST = $HostAddress
$env:LOCAL_DRAMA_PORT = [string]$Port
$env:LOCAL_DRAMA_NETWORK_MODE = $NetworkMode
$Process = Start-Process -FilePath $HostBinary -ArgumentList @("run") -WorkingDirectory $RepositoryRoot -WindowStyle Hidden -PassThru
$StatePath = Join-Path $RepositoryRoot "runtime\host-state.json"
for ($Attempt = 0; $Attempt -lt 240; $Attempt++) {
    Start-Sleep -Milliseconds 250
    if ($Process.HasExited) { throw "Runtime Host exited during startup with code $($Process.ExitCode)" }
    if (Test-Path -LiteralPath $StatePath) {
        $State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
        if ($State.status -eq "RUNNING") {
            Write-Output "LocalDramaStudio started via Runtime Host PID=$($Process.Id) API=$($State.api_pid) WORKER=$($State.worker_pid) http://${HostAddress}:$Port"
            exit 0
        }
        if ($State.status -eq "MAINTENANCE_FAILED") { throw "Startup database maintenance failed; run Host doctor and inspect logs" }
    }
}
& $HostBinary stop
throw "Runtime Host did not become ready within 60 seconds"
