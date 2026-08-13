$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Write-Output "repo=$repoRoot"
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python).Source }
Write-Output "python=$((& $python --version) 2>&1)"
Write-Output "node=$((node --version) 2>&1)"
Write-Output "pnpm=$((pnpm --version) 2>&1)"
Write-Output "mode=LOCAL_ONLY"
Write-Output "api_bind=127.0.0.1"
Write-Output "database=G2 not configured"
Write-Output "remote_provider=disabled"
Write-Output "legacy_migration=G11 deferred"
foreach ($relative in @('data','projects','work','cache','logs','backups','runtime')) {
  $path = Join-Path $repoRoot $relative
  Write-Output "$relative=$(Test-Path -LiteralPath $path) path=$path"
}
