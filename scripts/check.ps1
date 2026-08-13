param([switch]$IncludeComfyUI)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot
try {
  $python = Join-Path $repoRoot '.venv/Scripts/python.exe'
  if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
  & $python scripts/g0_validate.py
  & $python -m compileall -q apps/api scripts
  & $python scripts/generate_client.py
  Push-Location apps/api
  try {
    if ($IncludeComfyUI) {
      & $python -m pytest
    } else {
      & (Join-Path $repoRoot 'scripts/test_api_safe.ps1')
    }
    & (Join-Path $repoRoot '.venv/Scripts/mypy.exe') local_drama
  } finally {
    Pop-Location
  }
  & (Join-Path $repoRoot '.venv/Scripts/ruff.exe') check (Join-Path $repoRoot 'apps/api/local_drama') (Join-Path $repoRoot 'apps/api/tests') (Join-Path $repoRoot 'scripts')
  pnpm --dir apps/web build
  pnpm --dir apps/web test
  Write-Output 'G5 check passed'
} finally {
  Pop-Location
}
