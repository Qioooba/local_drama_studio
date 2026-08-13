$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw "Project Python runtime not found: $python"
}

Push-Location (Join-Path $repoRoot 'apps/api')
$previousComfyAccess = $env:LOCAL_DRAMA_COMFY_ACCESS
try {
  $env:LOCAL_DRAMA_COMFY_ACCESS = 'disabled'
  & $python -m pytest -m 'not comfyui'
  if ($LASTEXITCODE -ne 0) {
    throw "Non-ComfyUI API test suite failed with exit code $LASTEXITCODE"
  }
} finally {
  if ($null -eq $previousComfyAccess) {
    Remove-Item Env:LOCAL_DRAMA_COMFY_ACCESS -ErrorAction SilentlyContinue
  } else {
    $env:LOCAL_DRAMA_COMFY_ACCESS = $previousComfyAccess
  }
  Pop-Location
}
