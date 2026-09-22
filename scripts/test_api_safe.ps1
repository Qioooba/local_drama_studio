# Default, safe API test suite.
#
# Never contacts a live ComfyUI or a real GPU runtime.  Callers may append
# pytest arguments; when they supply their own marker filter the default marker
# expression is skipped so the two are not merged into a contradictory `-m`.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PytestArgs = @())

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw "Project Python runtime not found: $python"
}

$markerArguments = @('-m', 'not comfyui and not video_upscale_gpu')
if ($PytestArgs -contains '-m' -or $PytestArgs -contains '--markers') {
  $markerArguments = @()
}

Push-Location (Join-Path $repoRoot 'apps/api')
$previousComfyAccess = $env:LOCAL_DRAMA_COMFY_ACCESS
try {
  $env:LOCAL_DRAMA_COMFY_ACCESS = 'disabled'
  & $python -m pytest @markerArguments @PytestArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Safe (non-ComfyUI / non-GPU) API test suite failed with exit code $LASTEXITCODE"
  }
} finally {
  if ($null -eq $previousComfyAccess) {
    Remove-Item Env:LOCAL_DRAMA_COMFY_ACCESS -ErrorAction SilentlyContinue
  } else {
    $env:LOCAL_DRAMA_COMFY_ACCESS = $previousComfyAccess
  }
  Pop-Location
}
