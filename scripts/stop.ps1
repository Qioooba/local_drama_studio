$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pidPath = Join-Path $repoRoot 'runtime/api.pid.json'
$workerStopPath = Join-Path $repoRoot 'runtime/worker.stop'
if (-not (Test-Path -LiteralPath $pidPath)) {
  Write-Output 'LocalDramaStudio API is not tracked as running.'
  exit 0
}

$state = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$workerStopped = $false
if ($state.worker_pid) {
  $workerPid = [int]$state.worker_pid
  $workerProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$workerPid" -ErrorAction SilentlyContinue
  if ($workerProcess) {
    $workerCommand = [string]$workerProcess.CommandLine
    if ($workerCommand -notmatch 'scripts[\\/]run_worker\.py' -or $workerCommand -notmatch '--watch') {
      throw "Refusing to stop PID ${workerPid}: command line does not match tracked LocalDramaStudio Worker."
    }
    Set-Content -LiteralPath $workerStopPath -Value 'stop' -Encoding UTF8
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
      Start-Sleep -Milliseconds 100
      if (-not (Get-Process -Id $workerPid -ErrorAction SilentlyContinue)) { break }
    }
    if (Get-Process -Id $workerPid -ErrorAction SilentlyContinue) {
      Stop-Process -Id $workerPid -Force
    }
    $workerStopped = $true
  }
}
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
if ($workerStopped) { Write-Output "stopped LocalDramaStudio Worker PID=$workerPid" }
Remove-Item -LiteralPath $workerStopPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $pidPath -Force
