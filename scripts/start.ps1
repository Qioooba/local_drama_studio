param(
  [int]$Port = 3210
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $repoRoot 'runtime'
$pidPath = Join-Path $runtimeRoot 'api.pid.json'
$workerStopPath = Join-Path $runtimeRoot 'worker.stop'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

if (Test-Path -LiteralPath $pidPath) {
  $old = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
  $oldPid = if ($old.listener_pid) { [int]$old.listener_pid } else { [int]$old.pid }
  $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
  if ($oldProcess) {
    throw "LocalDramaStudio API already tracked as PID $oldPid; use scripts/stop.ps1 first."
  }
  Remove-Item -LiteralPath $pidPath -Force
}

$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
$migration = Start-Process -FilePath $python -ArgumentList @('-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head') -WorkingDirectory $repoRoot -WindowStyle Hidden -Wait -PassThru
if ($migration.ExitCode -ne 0) { throw "Database migration failed with exit code $($migration.ExitCode)." }
$arguments = @('-m', 'uvicorn', 'local_drama.main:app', '--app-dir', 'apps/api', '--host', '127.0.0.1', '--port', "$Port")
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
$listener = $null
for ($attempt = 0; $attempt -lt 100; $attempt++) {
  Start-Sleep -Milliseconds 100
  $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Where-Object LocalAddress -in @('127.0.0.1','::1') | Select-Object -First 1
  if ($connection) {
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$connection.OwningProcess)" -ErrorAction SilentlyContinue
    if ($candidate -and [string]$candidate.CommandLine -match 'local_drama\.main:app' -and [string]$candidate.CommandLine -match 'uvicorn') {
      $listener = $candidate
      break
    }
  }
  if ($process.HasExited) { throw "LocalDramaStudio API exited before binding port $Port." }
}
if (-not $listener) {
  Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
  throw "LocalDramaStudio API did not bind 127.0.0.1:$Port within 10 seconds."
}
$workerArguments = @(
  'scripts/run_worker.py',
  '--worker-id', 'local-drama-studio-main',
  '--channels', 'CPU,GPU_H3',
  '--watch',
  '--poll-seconds', '1',
  '--stop-file', $workerStopPath
)
$worker = Start-Process -FilePath $python -ArgumentList $workerArguments -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
Start-Sleep -Milliseconds 750
if ($worker.HasExited) {
  Stop-Process -Id $listener.ProcessId -Force -ErrorAction SilentlyContinue
  throw "LocalDramaStudio Worker exited during startup with exit code $($worker.ExitCode)."
}
$state = [ordered]@{
  pid = [int]$listener.ProcessId
  launcher_pid = $process.Id
  listener_pid = [int]$listener.ProcessId
  worker_pid = [int]$worker.Id
  start_time_utc = (Get-Date).ToUniversalTime().ToString('o')
  command = ($arguments -join ' ')
  worker_command = ($workerArguments -join ' ')
  instance = 'local-drama-studio'
}
$state | ConvertTo-Json | Set-Content -LiteralPath $pidPath -Encoding UTF8
Write-Output "started LocalDramaStudio API PID=$($listener.ProcessId) WORKER=$($worker.Id) LAUNCHER=$($process.Id) http://127.0.0.1:$Port"
