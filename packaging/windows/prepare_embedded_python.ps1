[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$EmbeddedPythonZip,
    [Parameter(Mandatory = $true)]
    [string]$Output
)

$ErrorActionPreference = "Stop"
$Archive = (Resolve-Path $EmbeddedPythonZip).Path
$Target = [System.IO.Path]::GetFullPath($Output)
if (Test-Path $Target) {
    throw "Output already exists: $Target"
}
New-Item -ItemType Directory -Path $Target | Out-Null
Expand-Archive -LiteralPath $Archive -DestinationPath $Target
$Python = Join-Path $Target "python.exe"
if (-not (Test-Path $Python -PathType Leaf)) {
    throw "The archive is not an official Windows embedded Python distribution"
}

# CPython's embedded _pth file intentionally ignores PYTHONPATH. The Runtime
# Host owns the child environment, so removing it enables the release-local
# app directory without installing Python globally.
Get-ChildItem -LiteralPath $Target -Filter "python*._pth" | Remove-Item -Force
$Version = (& $Python --version 2>&1 | Out-String).Trim()
if (-not $Version.StartsWith("Python 3.12.")) {
    throw "Python 3.12 embedded runtime required; got $Version"
}
Write-Output $Target
