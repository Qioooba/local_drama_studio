param(
  [string]$WorkerId = 'local-drama-studio-manual',
  [string]$Channels = 'CPU,GPU_H3,GPU_LOCAL_AI'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeRoot = Join-Path $repoRoot 'runtime'
$apiStatePath = Join-Path $runtimeRoot 'api.pid.json'
$standaloneStatePath = Join-Path $runtimeRoot 'worker.pid.json'
$workerStopPath = Join-Path $runtimeRoot 'worker.stop'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null

$trackedWorkerPid = $null
foreach ($statePath in @($apiStatePath, $standaloneStatePath)) {
  if (-not (Test-Path -LiteralPath $statePath)) { continue }
  $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
  if ($state.worker_pid) { $trackedWorkerPid = [int]$state.worker_pid; break }
}
if ($trackedWorkerPid) {
  $tracked = Get-CimInstance Win32_Process -Filter "ProcessId=$trackedWorkerPid" -ErrorAction SilentlyContinue
  if ($tracked -and [string]$tracked.CommandLine -match 'scripts[\\/]run_worker\.py' -and [string]$tracked.CommandLine -match '--watch') {
    Write-Output "LocalDramaStudio Worker already running PID=$trackedWorkerPid"
    exit 0
  }
}

$pythonCandidates = @(
  (Join-Path $repoRoot '.venv/Scripts/python.exe'),
  (Join-Path $repoRoot 'apps/api/.venv/Scripts/python.exe')
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) { $python = (Get-Command python).Source }
Remove-Item -LiteralPath $workerStopPath -Force -ErrorAction SilentlyContinue
$workerArguments = @(
  'scripts/run_worker.py',
  '--worker-id', $WorkerId,
  '--channels', $Channels,
  '--watch',
  '--poll-seconds', '1',
  '--stop-file', $workerStopPath
)
$worker = Start-Process -FilePath $python -ArgumentList $workerArguments -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
Start-Sleep -Milliseconds 750
if ($worker.HasExited) { throw "LocalDramaStudio Worker exited during startup with exit code $($worker.ExitCode)." }

$workerCommand = $workerArguments -join ' '
if (Test-Path -LiteralPath $apiStatePath) {
  $state = Get-Content -LiteralPath $apiStatePath -Raw | ConvertFrom-Json
  $state | Add-Member -NotePropertyName worker_pid -NotePropertyValue ([int]$worker.Id) -Force
  $state | Add-Member -NotePropertyName worker_command -NotePropertyValue $workerCommand -Force
  $state | ConvertTo-Json | Set-Content -LiteralPath $apiStatePath -Encoding UTF8
} else {
  [ordered]@{
    worker_pid = [int]$worker.Id
    worker_command = $workerCommand
    start_time_utc = (Get-Date).ToUniversalTime().ToString('o')
  } | ConvertTo-Json | Set-Content -LiteralPath $standaloneStatePath -Encoding UTF8
}
Write-Output "started LocalDramaStudio Worker PID=$($worker.Id) CHANNELS=$Channels"
