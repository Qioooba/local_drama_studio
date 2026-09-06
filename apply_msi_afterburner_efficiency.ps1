$ErrorActionPreference = 'Stop'

$afterburnerPath = 'C:\Program Files (x86)\MSI Afterburner\MSIAfterburner.exe'
$liveProfilePath = 'C:\Program Files (x86)\MSI Afterburner\Profiles\VEN_10DE&DEV_2203&SUBSYS_13017377&REV_A1&BUS_4&DEV_0&FN_0.cfg'
$auditDirectory = 'F:\AI_Projects\h3\local_drama_studio\.codex_backups\msi-afterburner-20260906'
$preparedProfilePath = Join-Path $auditDirectory 'reviewed-gpu-profile.cfg'
$preparedGlobalPath = Join-Path $auditDirectory 'reviewed-global-profile.cfg'
$liveGlobalPath = 'C:\Program Files (x86)\MSI Afterburner\Profiles\MSIAfterburner.cfg'
$backupDirectory = Join-Path $auditDirectory ('before-review-apply-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$resultPath = Join-Path $auditDirectory 'review-apply-result.json'

if (-not (Test-Path -LiteralPath $afterburnerPath)) {
    throw "MSI Afterburner not found: $afterburnerPath"
}
if (-not (Test-Path -LiteralPath $liveProfilePath)) {
    throw "Active GPU profile not found: $liveProfilePath"
}
if (-not (Test-Path -LiteralPath $preparedProfilePath)) {
    throw "Prepared profile not found: $preparedProfilePath"
}

if (-not (Test-Path -LiteralPath $preparedGlobalPath)) { throw 'Prepared global configuration missing' }
$identity = & nvidia-smi -i '00000000:04:00.0' --query-gpu=name,power.default_limit --format=csv,noheader,nounits
if ($LASTEXITCODE -ne 0 -or $identity -notmatch 'RTX 3090 Ti, 480\.00') { throw 'GPU identity or default power changed' }
New-Item -ItemType Directory -Path $backupDirectory | Out-Null
$backupProfilePath = Join-Path $backupDirectory 'gpu.cfg'
$backupGlobalPath = Join-Path $backupDirectory 'global.cfg'
Copy-Item -LiteralPath $liveProfilePath -Destination $backupProfilePath
Copy-Item -LiteralPath $liveGlobalPath -Destination $backupGlobalPath
$existingTask = Get-ScheduledTask -TaskName 'MSIAfterburner' -TaskPath '\'
if (@($existingTask.Actions).Count -ne 1 -or $existingTask.Actions[0].Execute -ne $afterburnerPath) { throw 'Unexpected Afterburner startup task' }
$originalActions = $existingTask.Actions
Export-ScheduledTask -TaskName 'MSIAfterburner' -TaskPath '\' | Set-Content -LiteralPath (Join-Path $backupDirectory 'startup-task.xml') -Encoding Unicode
$taskChanged = $false
try {
    Stop-Process -Name 'MSIAfterburner' -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Copy-Item -LiteralPath $preparedProfilePath -Destination $liveProfilePath -Force
    Copy-Item -LiteralPath $preparedGlobalPath -Destination $liveGlobalPath -Force
    # Keep the existing trigger, principal and settings; explicitly load Profile 2.
    $newActionParameters = @{Execute=$afterburnerPath; Argument='/s -Profile2'}
    if ($originalActions[0].WorkingDirectory) { $newActionParameters.WorkingDirectory = $originalActions[0].WorkingDirectory }
    $newAction = New-ScheduledTaskAction @newActionParameters
    Set-ScheduledTask -TaskName 'MSIAfterburner' -TaskPath '\' -Action $newAction | Out-Null
    $taskChanged = $true
    Start-ScheduledTask -TaskName 'MSIAfterburner' -TaskPath '\'
    Start-Sleep -Seconds 7
    $powerReadback = & nvidia-smi -i '00000000:04:00.0' --query-gpu=power.limit --format=csv,noheader,nounits
    if ($LASTEXITCODE -ne 0 -or [Math]::Abs([double]$powerReadback - 350.4) -gt 0.2) {
        throw "Startup power validation failed: $powerReadback"
    }
    $map = [IO.MemoryMappedFiles.MemoryMappedFile]::OpenExisting('MACMSharedMemory', [IO.MemoryMappedFiles.MemoryMappedFileRights]::Read)
    $view = $map.CreateViewAccessor(0, 0, [IO.MemoryMappedFiles.MemoryMappedFileAccess]::Read)
    try {
        if ($view.ReadUInt32(0) -ne 0x4D41434D -or $view.ReadUInt32(4) -lt 0x00020001) { throw 'Unsupported control memory format' }
        $entry = $view.ReadUInt32(8)
        $fanFlags = $view.ReadUInt32($entry + 56)
        $coreOffset = $view.ReadInt32($entry + 188)
        $memoryOffset = $view.ReadInt32($entry + 204)
        $thermalTarget = $view.ReadInt32($entry + 220)
        if (($fanFlags -band 1) -ne 1 -or $coreOffset -ne 0 -or $memoryOffset -ne 0 -or $thermalTarget -ne 78) {
            throw "Control validation failed: fan=$fanFlags core=$coreOffset memory=$memoryOffset temperature=$thermalTarget"
        }
    } finally { $view.Dispose(); $map.Dispose() }
    [pscustomobject]@{
        Status = 'applied-and-verified'; AppliedAt = (Get-Date -Format o)
        PowerLimitW = [double]$powerReadback; FanAutomatic = $true
        CoreOffsetKHz = $coreOffset; MemoryOffsetKHz = $memoryOffset; ThermalTargetC = $thermalTarget
        StartupArgument = '/s -Profile2'; BackupDirectory = $backupDirectory
        GPUProfileSHA256 = (Get-FileHash -LiteralPath $liveProfilePath -Algorithm SHA256).Hash
    } | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
} catch {
    $failure = $_.Exception.Message
    if ($taskChanged) { Set-ScheduledTask -TaskName 'MSIAfterburner' -TaskPath '\' -Action $originalActions | Out-Null }
    Stop-Process -Name 'MSIAfterburner' -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Copy-Item -LiteralPath $backupProfilePath -Destination $liveProfilePath -Force
    Copy-Item -LiteralPath $backupGlobalPath -Destination $liveGlobalPath -Force
    Start-Process -FilePath $afterburnerPath -ArgumentList '-Profile2' -WindowStyle Hidden
    [pscustomobject]@{Status='rolled-back'; Error=$failure; BackupDirectory=$backupDirectory} | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    throw
}
