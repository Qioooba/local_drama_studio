param(
  [switch]$IncludeComfyUI,
  [switch]$IncludeBlueprintAudit,
  [string]$BlueprintRoot = '',
  [string]$ModelManifest = ''
)

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
  & $python scripts/check_dependency_locks.py
  Assert-NativeSuccess 'dependency lock validation'
  if ($IncludeBlueprintAudit) {
    # Opt-in: the external blueprint and the real machine model manifest live
    # OUTSIDE this repository.  Never required by the default, portable gate.
    $blueprintArgs = @()
    if ($BlueprintRoot) { $blueprintArgs += @('--blueprint-root', $BlueprintRoot) }
    if ($ModelManifest) { $blueprintArgs += @('--manifest', $ModelManifest) }
    & $python scripts/g0_validate.py @blueprintArgs
    Assert-NativeSuccess 'in-repo contract validation with external blueprint audit'
  } else {
    & $python scripts/g0_validate.py
    Assert-NativeSuccess 'in-repo contract validation'
  }
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
  & $python scripts/maintainability_audit.py
  Assert-NativeSuccess 'maintainability and regression-contract audit'
  pnpm --dir apps/web build
  Assert-NativeSuccess 'Web production build'
  pnpm --dir apps/web test
  Assert-NativeSuccess 'Web tests'
  Write-Output 'G5 check passed'
} finally {
  Pop-Location
}
