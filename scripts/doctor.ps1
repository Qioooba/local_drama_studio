$ErrorActionPreference = 'Continue'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Write-Host "=== LocalDramaStudio V2 本机环境与健康诊断 (Doctor) ===" -ForegroundColor Cyan
Write-Host ("项目根目录: " + $repoRoot)

# 1. 基础环境运行时
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
$pyVer = if ($python) { (& $python --version 2>&1) } else { "未检测到 Python" }
$nodeVer = try { (node --version 2>&1) } catch { "未检测到 Node.js" }
$pnpmVer = try { (pnpm --version 2>&1) } catch { "未检测到 pnpm" }
Write-Host ("[Runtime] Python: " + $pyVer + " (" + $python + ")")
Write-Host ("[Runtime] Node.js: " + $nodeVer)
Write-Host ("[Runtime] pnpm: " + $pnpmVer)

# 2. 媒体处理引擎 (FFmpeg / FFprobe)
$ffmpegCmd = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source
$ffprobeCmd = (Get-Command ffprobe -ErrorAction SilentlyContinue).Source
$ffmpegCandidate = if ($env:LOCAL_DRAMA_FFMPEG) { $env:LOCAL_DRAMA_FFMPEG } else { $ffmpegCmd }
$ffprobeCandidate = if ($env:LOCAL_DRAMA_FFPROBE) { $env:LOCAL_DRAMA_FFPROBE } else { $ffprobeCmd }
if ($ffmpegCandidate) {
  Write-Host ("[Media] FFmpeg: PASS (" + $ffmpegCandidate + ")") -ForegroundColor Green
} else {
  Write-Host "[Media] FFmpeg: WARN (未通过 LOCAL_DRAMA_FFMPEG 或 PATH 找到 ffmpeg；视频抽帧与联系表可能受限)" -ForegroundColor Yellow
}
if ($ffprobeCandidate) {
  Write-Host ("[Media] FFprobe: PASS (" + $ffprobeCandidate + ")") -ForegroundColor Green
} else {
  Write-Host "[Media] FFprobe: WARN (未通过 LOCAL_DRAMA_FFPROBE 或 PATH 找到 ffprobe；媒体探测可能受限)" -ForegroundColor Yellow
}

# 3. 数据库与迁移状态
$dbPath = Join-Path $repoRoot 'data/local_drama.sqlite3'
if (Test-Path -LiteralPath $dbPath) {
  $dbSize = (Get-Item -LiteralPath $dbPath).Length
  Write-Host ("[Database] SQLite: PASS (路径: " + $dbPath + ", 大小: " + [math]::Round($dbSize/1MB, 2) + " MB)") -ForegroundColor Green
  if ($python) {
    $alembicHead = try { (& $python -m alembic -c alembic.ini current 2>&1) } catch { "检查失败" }
    Write-Host ("[Database] Migration: " + $alembicHead)
  }
} else {
  Write-Host "[Database] SQLite: 未初始化 (运行 scripts/start.ps1 或 alembic upgrade head 自动建表)" -ForegroundColor Yellow
}

# 4. 端口与后台服务联通性 (8188 ComfyUI, 11434 Ollama)
$comfyConn = Test-NetConnection -ComputerName 127.0.0.1 -Port 8188 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($comfyConn) {
  Write-Host "[Loopback] ComfyUI (127.0.0.1:8188): 在线 (Online)" -ForegroundColor Green
} else {
  Write-Host "[Loopback] ComfyUI (127.0.0.1:8188): 离线 (Offline - 生成时将排队或降级本地预览)" -ForegroundColor Yellow
}

$llmConn = Test-NetConnection -ComputerName 127.0.0.1 -Port 11434 -InformationLevel Quiet -WarningAction SilentlyContinue
if ($llmConn) {
  Write-Host "[Loopback] Ollama LLM (127.0.0.1:11434): 在线 (Online)" -ForegroundColor Green
} else {
  Write-Host "[Loopback] Ollama LLM (127.0.0.1:11434): 离线 (Offline - 建议启动本机 Ollama)" -ForegroundColor Yellow
}

# 5. 受控目录健康检查
Write-Host "--- 目录空间检查 ---"
foreach ($relative in @('data','projects','work','cache','logs','backups','runtime')) {
  $path = Join-Path $repoRoot $relative
  $exists = Test-Path -LiteralPath $path
  if (-not $exists) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
  Write-Host ("  [" + $relative + "]: OK (" + $path + ")")
}
Write-Host "=== 诊断完成 ===" -ForegroundColor Cyan
