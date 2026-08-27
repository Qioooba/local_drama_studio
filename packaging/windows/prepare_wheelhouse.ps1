[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Output
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
python (Join-Path $RepositoryRoot "packaging\common\prepare_wheelhouse.py") $Output
if ($LASTEXITCODE -ne 0) { throw "wheelhouse preparation failed" }
