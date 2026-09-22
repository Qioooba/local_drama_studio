# Start the isolated Qwen-Image-2.1 ComfyUI runtime on 127.0.0.1:8189.
#
# This is a managed foreground process. Stop it with Ctrl-C.
#
#   pwsh -File scripts/qwen21/start_comfy_qwen21.ps1
#
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$pin = Get-Content (Join-Path $repoRoot 'config\comfyui-qwen21-runtime.json') -Raw | ConvertFrom-Json

$installRoot = [IO.Path]::GetFullPath($pin.install_root)
$checkout = Join-Path $installRoot 'ComfyUI'
$venvPython = Join-Path $installRoot 'venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) { throw "runtime not installed; run scripts/qwen21/install_comfy_runtime.ps1 first ($venvPython missing)" }

# One physical GPU: refuse to start a second heavy runtime while 8188 is busy.
$busy = $null
try {
    $queue = Invoke-RestMethod -Uri "http://127.0.0.1:8188/queue" -TimeoutSec 4
    $busy = @($queue.queue_running).Count + @($queue.queue_pending).Count
} catch {
    Write-Host "[info] production runtime on 8188 is not reachable (that is fine)."
}
if ($busy -and $busy -gt 0) {
    throw "production ComfyUI on 8188 has $busy running/pending prompt(s); refusing to start a second GPU-heavy runtime on the same card."
}

$ioRoot = [IO.Path]::GetFullPath($pin.io_root)
foreach ($sub in @('input', 'output', 'temp', 'user')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $ioRoot $sub) | Out-Null
}

Write-Host "starting $($pin.runtime_code) on http://$($pin.host):$($pin.port)"
Write-Host "  checkout : $checkout"
Write-Host "  io_root  : $ioRoot"
Write-Host ""

$arguments = @('main.py') + $pin.launch_args + @(
    '--input-directory', (Join-Path $ioRoot 'input'),
    '--output-directory', (Join-Path $ioRoot 'output'),
    '--temp-directory', (Join-Path $ioRoot 'temp'),
    '--user-directory', (Join-Path $ioRoot 'user')
)

Push-Location $checkout
try {
    & $venvPython @arguments
} finally {
    Pop-Location
}
