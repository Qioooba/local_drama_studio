param(
  [ValidateSet('start','stop','status')]
  [string]$Action = 'status'
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $repoRoot 'runtime'
$statePath = Join-Path $runtimeDir 'comfy.pid.json'
$legacyPidPath = Join-Path $runtimeDir 'comfy.pid'
$sandboxRoot = Join-Path $repoRoot 'work/comfy-production'
$outputRoot = Join-Path $sandboxRoot 'output'
$inputRoot = Join-Path $sandboxRoot 'input'
$tempRoot = Join-Path $sandboxRoot 'temp'
$userRoot = Join-Path $sandboxRoot 'user'
$logsRoot = Join-Path $repoRoot 'logs'
$stdoutPath = Join-Path $logsRoot 'comfy-production.stdout.log'
$stderrPath = Join-Path $logsRoot 'comfy-production.stderr.log'
$managerConfigDir = Join-Path $userRoot '__manager'
$managerConfigPath = Join-Path $managerConfigDir 'config.ini'
$port = if ($env:LOCAL_DRAMA_COMFY_PORT) { [int]$env:LOCAL_DRAMA_COMFY_PORT } else { 8188 }

function Get-ListenerProcess {
  $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | Where-Object LocalAddress -in @('127.0.0.1','::1') | Select-Object -First 1
  if ($null -eq $listener) { return $null }
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)" -ErrorAction SilentlyContinue
  if ($null -eq $process -or $process.CommandLine -notmatch 'main\.py' -or $process.CommandLine -notmatch "--port\s+$port") { return $null }
  return $process
}

function Read-State {
  if (-not (Test-Path -LiteralPath $statePath)) { return $null }
  try { return Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json } catch { return $null }
}

if ($Action -eq 'status') {
  $listener = Get-ListenerProcess
  if ($null -eq $listener) { Write-Output 'COMFY_NOT_RUNNING'; exit 0 }
  $state = Read-State
  $ownership = if ($null -ne $state -and [int]$state.listener_pid -eq [int]$listener.ProcessId) { 'TRACKED' } else { 'UNTRACKED' }
  Write-Output "COMFY_RUNNING PID=$($listener.ProcessId) PORT=$port OWNERSHIP=$ownership OUTPUT=$outputRoot"
  exit 0
}

if ($Action -eq 'stop') {
  $state = Read-State
  $listener = Get-ListenerProcess
  if ($null -eq $state -or $null -eq $listener -or [int]$state.listener_pid -ne [int]$listener.ProcessId) {
    Write-Output 'COMFY_NOT_TRACKED'; exit 0
  }
  $targets = @([int]$state.listener_pid, [int]$state.launcher_pid) | Select-Object -Unique
  foreach ($target in $targets) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $target" -ErrorAction SilentlyContinue
    if ($null -ne $process -and $process.CommandLine -match 'main\.py' -and $process.CommandLine -match "--port\s+$port") {
      Stop-Process -Id $target -Force -ErrorAction SilentlyContinue
    }
  }
  for ($attempt = 0; $attempt -lt 20; $attempt++) {
    if ($null -eq (Get-ListenerProcess)) { break }
    Start-Sleep -Milliseconds 250
  }
  Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $legacyPidPath -Force -ErrorAction SilentlyContinue
  Write-Output "COMFY_STOPPED PID=$($listener.ProcessId)"
  exit 0
}

$python = $env:LOCAL_DRAMA_COMFY_PYTHON
$comfyRoot = $env:LOCAL_DRAMA_COMFY_ROOT
if ([string]::IsNullOrWhiteSpace($python) -or [string]::IsNullOrWhiteSpace($comfyRoot)) {
  throw 'start requires LOCAL_DRAMA_COMFY_PYTHON and LOCAL_DRAMA_COMFY_ROOT; no hidden ComfyUI path is assumed'
}
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath (Join-Path $comfyRoot 'main.py'))) { throw 'configured ComfyUI python/root does not exist' }
$existing = Get-ListenerProcess
if ($null -ne $existing) { Write-Output "COMFY_ALREADY_RUNNING PID=$($existing.ProcessId) PORT=$port"; exit 0 }

New-Item -ItemType Directory -Force -Path $runtimeDir,$outputRoot,$inputRoot,$tempRoot,$userRoot,$logsRoot | Out-Null
New-Item -ItemType Directory -Force -Path $managerConfigDir | Out-Null
if (-not (Test-Path -LiteralPath $managerConfigPath)) {
  @('[default]','network_mode = offline','db_mode = cache','file_logging = True','model_download_by_agent = False','allow_git_url_install = False','allow_pip_install = False') | Set-Content -LiteralPath $managerConfigPath -Encoding UTF8
} else {
  $managerConfig = Get-Content -LiteralPath $managerConfigPath -Raw
  if ($managerConfig -match '(?m)^network_mode\s*=') {
    $managerConfig = $managerConfig -replace '(?m)^network_mode\s*=.*$', 'network_mode = offline'
  } else {
    $managerConfig += "`r`nnetwork_mode = offline`r`n"
  }
  Set-Content -LiteralPath $managerConfigPath -Value $managerConfig -Encoding UTF8
}
$arguments = @(
  'main.py','--listen','127.0.0.1','--port',"$port",
  '--output-directory',$outputRoot,'--input-directory',$inputRoot,'--temp-directory',$tempRoot,'--user-directory',$userRoot,
  # The platform's H3 chain uses ComfyUI core + comfy_extras nodes only; the RH
  # plugin family is excluded (verified to crash on this host) and no custom
  # nodes are whitelisted.
  '--disable-all-custom-nodes','--disable-api-nodes'
)
if ($env:LOCAL_DRAMA_COMFY_DIAGNOSTIC_FLAGS) {
  $allowedDiagnosticFlags = @(
    '--disable-dynamic-vram','--disable-cuda-malloc','--disable-smart-memory',
    '--lowvram','--cpu-vae','--force-fp16','--force-fp32'
  )
  $requestedDiagnosticFlags = @($env:LOCAL_DRAMA_COMFY_DIAGNOSTIC_FLAGS -split '\s+' | Where-Object { $_ })
  $invalidDiagnosticFlags = @($requestedDiagnosticFlags | Where-Object { $_ -notin $allowedDiagnosticFlags })
  if ($invalidDiagnosticFlags.Count -gt 0) {
    throw "unsupported LOCAL_DRAMA_COMFY_DIAGNOSTIC_FLAGS: $($invalidDiagnosticFlags -join ', ')"
  }
  $arguments += $requestedDiagnosticFlags
}
$env:LOCAL_DRAMA_H3_EPHEMERAL_WORKER = '1'
$launcher = Start-Process -FilePath $python -WorkingDirectory $comfyRoot -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$listener = $null
for ($attempt = 0; $attempt -lt 120; $attempt++) {
  Start-Sleep -Seconds 1
  $listener = Get-ListenerProcess
  if ($null -ne $listener) { break }
  if ($launcher.HasExited) { throw "ComfyUI exited during startup; inspect $stderrPath" }
}
if ($null -eq $listener) {
  Stop-Process -Id $launcher.Id -Force -ErrorAction SilentlyContinue
  throw "ComfyUI did not bind 127.0.0.1:$port within 120 seconds; inspect $stderrPath"
}
$state = [ordered]@{
  launcher_pid = $launcher.Id
  listener_pid = [int]$listener.ProcessId
  port = $port
  started_at = (Get-Date).ToUniversalTime().ToString('o')
  output_root = $outputRoot
  input_root = $inputRoot
  temp_root = $tempRoot
  user_root = $userRoot
  stdout_log = $stdoutPath
  stderr_log = $stderrPath
  lifecycle = 'EPHEMERAL_SINGLE_JOB'
  skip_vae_exit_offload = $true
}
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
Remove-Item -LiteralPath $legacyPidPath -Force -ErrorAction SilentlyContinue
Write-Output "COMFY_STARTED PID=$($listener.ProcessId) PORT=$port OUTPUT=$outputRoot"
