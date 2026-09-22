# Real-hardware API suite: LIVE ComfyUI / NCNN-Vulkan upscale cases.
#
# This is the EXPLICIT opt-in counterpart to scripts/test_api_safe.ps1.  It is
# never part of `pnpm run api:test` or `pnpm run check`, because it contacts a
# runtime the operator owns.
#
# Prerequisites (all must already be true; nothing is downloaded or started):
#   * Windows x64 with a working NVIDIA GPU driver.
#   * ComfyUI listening on loopback (default http://127.0.0.1:8188) with the H3
#     model files installed, and LOCAL_DRAMA_COMFY_ACCESS set to `enabled`.
#   * For the `video_upscale_gpu` cases: a configured real NCNN/Vulkan runtime,
#     its model files on disk, and enough free VRAM.
#   * The machine model manifest reachable at LOCAL_DRAMA_MODEL_ROOT or the
#     default manifest path; these cases do NOT run against a synthetic manifest.
#
# Callers may pass additional pytest arguments through to this script.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PytestArgs = @())

$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) {
  throw "Project Python runtime not found: $python"
}

$markerArguments = @('-m', 'comfyui or video_upscale_gpu')
if ($PytestArgs -contains '-m') {
  $markerArguments = @()
}

Push-Location (Join-Path $repoRoot 'apps/api')
if (-not $env:LOCAL_DRAMA_COMFY_ACCESS) {
  $env:LOCAL_DRAMA_COMFY_ACCESS = 'enabled'
}
try {
  Write-Output 'api:test:live - contacting the operator-owned local ComfyUI / GPU runtime'
  & $python -m pytest @markerArguments @PytestArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Live ComfyUI / GPU API test suite failed with exit code $LASTEXITCODE"
  }
} finally {
  Pop-Location
}
