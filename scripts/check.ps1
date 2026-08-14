param([switch]$IncludeComfyUI)

$ErrorActionPreference = 'Stop'

function Assert-NativeSuccess([string]$Step) {
  if ($LASTEXITCODE -ne 0) {
    throw "$Step failed with exit code $LASTEXITCODE"
  }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Push-Location $repoRoot
try {
  $python = Join-Path $repoRoot '.venv/Scripts/python.exe'
  if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
  & $python scripts/g0_validate.py
  Assert-NativeSuccess 'blueprint validation'
  & $python -m compileall -q apps/api scripts
  Assert-NativeSuccess 'Python compileall'
  & $python scripts/generate_client.py
  Assert-NativeSuccess 'OpenAPI/client generation'
  Push-Location apps/api
  try {
    if ($IncludeComfyUI) {
      & $python -m pytest
      Assert-NativeSuccess 'API tests including ComfyUI'
    } else {
      & (Join-Path $repoRoot 'scripts/test_api_safe.ps1')
      Assert-NativeSuccess 'safe API tests'
    }
    & (Join-Path $repoRoot '.venv/Scripts/mypy.exe') local_drama
    Assert-NativeSuccess 'mypy'
  } finally {
    Pop-Location
  }
  & (Join-Path $repoRoot '.venv/Scripts/ruff.exe') check (Join-Path $repoRoot 'apps/api/local_drama') (Join-Path $repoRoot 'apps/api/tests') (Join-Path $repoRoot 'scripts')
  Assert-NativeSuccess 'Ruff'
  pnpm --dir apps/web build
  Assert-NativeSuccess 'Web production build'
  pnpm --dir apps/web test
  Assert-NativeSuccess 'Web tests'
  Write-Output 'G5 check passed'
} finally {
  Pop-Location
}
