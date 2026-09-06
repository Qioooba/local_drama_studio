# start-llama-gateway.ps1
# Single-instance launcher for local_drama.entrypoints.llama_gateway.
# Refuses to start if the public port (28088) or internal port (28089) is
# already owned by a live llama-server / llama_gateway, to stop two parallel
# gateways from concurrently loading the same GGUF into VRAM.
#
# Usage:
#   powershell -NoProfile -File scripts\start-llama-gateway.ps1
#
# Override port:  $env:LOCAL_DRAMA_LLAMA_GATEWAY_PORT / _LLAMA_SERVER_PORT
# Override python: $env:LAUNCHER_PYTHON (defaults to the project venv)

$ErrorActionPreference = 'Stop'

$ProjectRoot = (Resolve-Path "$PSScriptRoot\..").Path
$ConfigPath  = Join-Path $ProjectRoot 'config\config.json'
$LogDir      = Join-Path $ProjectRoot 'logs'
$OutLog      = Join-Path $LogDir 'llama-gateway-launcher.out.log'
$ErrLog      = Join-Path $LogDir 'llama-gateway-launcher.err.log'
$LockPath    = Join-Path $LogDir 'llama-gateway.launcher.lock'
$StaleSec    = 600   # lock considered stale after 10 min (no heartbeat in crash)

if (-not (Test-Path $LogDir)) { [void](New-Item -ItemType Directory -Path $LogDir) }
"" | Out-File $OutLog -Encoding utf8
"" | Out-File $ErrLog -Encoding utf8

function Log($msg) {
  $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
  Add-Content -Path $OutLog -Value $line -Encoding utf8
  Write-Host $line
}

Log "==== launcher start ===="
Log "ProjectRoot: $ProjectRoot"
Log "Config: $ConfigPath"

# --- 1. resolve python interpreter (prefer venv, then $env:LAUNCHER_PYTHON) ---
$python = $env:LAUNCHER_PYTHON
if (-not $python) {
  $venvPy = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
  if (Test-Path $venvPy) { $python = $venvPy }
  else {
    # last resort: the system-wide Python on PATH
    $python = (Get-Command python.exe -EA SilentlyContinue).Source
  }
}
if (-not $python -or -not (Test-Path $python)) {
  Log "FATAL: no python interpreter found (set `$env:LAUNCHER_PYTHON)"
  exit 2
}
Log "Python: $python"

# --- 2. read ports from config (so we don't hard-code them) ---
try {
  $cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
  $gwPort  = [int]$cfg.runtime.llama_gateway_port
  $llPort  = [int]$cfg.runtime.llama_server_port
} catch {
  Log "FATAL: cannot read config.json: $_"
  exit 2
}
Log "Ports: gateway=$gwPort  llama-server=$llPort"

# --- 3. check if a healthy llama_gateway / llama-server already owns them ---
function Test-PortOwner([int]$port) {
  $conn = Get-NetTCPConnection -LocalPort $port -State Listen -EA SilentlyContinue | Select-Object -First 1
  if (-not $conn) { return $null }
  $proc = Get-Process -Id $conn.OwningProcess -EA SilentlyContinue
  return $proc
}

$existingGw = Test-PortOwner $gwPort
$existingLl = Test-PortOwner $llPort
if ($existingGw) {
  Log "ABORT: port $gwPort already owned by PID $($existingGw.Id) ($($existingGw.ProcessName)). Refusing to start a second gateway."
  exit 3
}
if ($existingLl) {
  $pidFile = Join-Path $LogDir 'llama\llama_server.pid'
  $pidVal  = (Get-Content $pidFile -Raw -EA SilentlyContinue).Trim()
  Log "WARN: port $llPort owned by PID $($existingLl.Id) ($($existingLl.ProcessName)), pidfile=$pidVal. New gateway will adopt via pidfile if it matches."
}

# --- 4. acquire file lock (with stale-detection) ---
function Acquire-Lock {
  if (Test-Path $LockPath) {
    $age = (Get-Date) - (Get-Item $LockPath).LastWriteTime
    if ($age.TotalSeconds -gt $StaleSec) {
      Log "Lock stale (age $([int]$age.TotalSeconds)s), removing"
      Remove-Item $LockPath -Force
    } else {
      $holder = (Get-Content $LockPath -Raw).Trim()
      Log "ABORT: lock held by '$holder' (age $([int]$age.TotalSeconds)s). Another launcher is already active."
      return $false
    }
  }
  [void](New-Item -ItemType File -Path $LockPath -Value "pid=$PID host=$env:COMPUTERNAME started=$(Get-Date -Format o)" -Force)
  return $true
}
if (-not (Acquire-Lock)) { exit 3 }

# --- 5. launch the gateway as a detached child of THIS shell (so the lock
#        dies with us, and on next start we re-check the port) ---
$env:LOCAL_DRAMA_CONFIG = $ConfigPath
$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $python
$psi.Arguments = "-m local_drama.entrypoints.llama_gateway --config `"$ConfigPath`""
$psi.WorkingDirectory = $ProjectRoot
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $false
$psi.RedirectStandardError  = $false

try {
  $proc = [System.Diagnostics.Process]::Start($psi)
} catch {
  Log "FATAL: failed to spawn gateway: $_"
  Remove-Item $LockPath -Force -EA SilentlyContinue
  exit 2
}
Log "Spawned gateway PID=$($proc.Id)  python=$python"

# --- 6. wait for /health (max 30s, since model is already loaded we just
#        wait for the FastAPI app to bind) ---
$healthy = $false
for ($i=1; $i -le 15; $i++) {
  Start-Sleep -Seconds 2
  try {
    $r = Invoke-WebRequest "http://127.0.0.1:$gwPort/health" -UseBasicParsing -TimeoutSec 2
    if ($r.StatusCode -eq 200) {
      Log "Gateway healthy after ${i}*2s: $($r.Content)"
      $healthy = $true
      break
    }
  } catch { }
}
if (-not $healthy) {
  Log "Gateway did not become healthy in 30s; child still running, will be adopted by next launcher call"
}

# --- 7. detach: write the child PID to a pidfile for later cleanup, then
#        exit (the python child keeps running because it has no job object
#        binding to this shell) ---
$childPidPath = Join-Path $LogDir 'llama-gateway.launcher.pid'
"$($proc.Id)" | Out-File $childPidPath -Encoding utf8
Log "Child PID written to $childPidPath. Launcher exiting (child detached)."
exit 0
