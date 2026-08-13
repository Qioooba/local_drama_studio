$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidPath = Join-Path $repoRoot 'runtime/api.pid.json'
if (-not (Test-Path -LiteralPath $pidPath)) {
  Write-Output 'LocalDramaStudio API is not tracked as running.'
  exit 0
}

$state = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$targets = @()
if ($state.listener_pid) { $targets += [int]$state.listener_pid }
if ($state.launcher_pid) { $targets += [int]$state.launcher_pid }
if (-not $targets.Count) { $targets += [int]$state.pid }
$listenerConnection = Get-NetTCPConnection -State Listen -LocalPort 3210 -ErrorAction SilentlyContinue | Where-Object LocalAddress -in @('127.0.0.1','::1') | Select-Object -First 1
if ($listenerConnection) {
  $listenerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$listenerConnection.OwningProcess)" -ErrorAction SilentlyContinue
  if ($listenerProcess -and [string]$listenerProcess.CommandLine -match 'local_drama\.main:app' -and [string]$listenerProcess.CommandLine -match 'uvicorn') {
    $targets += [int]$listenerProcess.ProcessId
  }
}
$stopped = @()
foreach ($target in ($targets | Select-Object -Unique)) {
  $process = Get-CimInstance Win32_Process -Filter "ProcessId=$target" -ErrorAction SilentlyContinue
  if ($process) {
    $commandLine = [string]$process.CommandLine
    if ($commandLine -notmatch 'local_drama\.main:app' -or $commandLine -notmatch 'uvicorn') {
      throw "Refusing to stop PID ${target}: command line does not match tracked LocalDramaStudio API."
    }
    Stop-Process -Id $target -Force
    $stopped += $target
  }
}
if ($stopped.Count) { Write-Output "stopped LocalDramaStudio API PID=$($stopped -join ',')" }
else { Write-Output "tracked API processes are not running" }
Remove-Item -LiteralPath $pidPath -Force
