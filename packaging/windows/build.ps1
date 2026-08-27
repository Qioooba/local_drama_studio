[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$PythonRuntime,
    [Parameter(Mandatory = $true)]
    [string]$FFmpegRuntime,
    [string]$Wheelhouse = "",
    [string]$Output = "",
    [string]$SigningPrivateKey = "",
    [string]$SigningPublicKey = "",
    [string]$CodeSigningCertificateThumbprint = "",
    [switch]$OnlineDependencies,
    [switch]$SkipWeb,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $Output) {
    $Output = Join-Path $RepositoryRoot "dist"
}
$ReleaseIdentity = Get-Content (Join-Path $RepositoryRoot "release\version.json") -Raw | ConvertFrom-Json
$ReleaseVersion = $ReleaseIdentity.version
if ($ReleaseIdentity.channel -ne "development" -and (-not $SigningPrivateKey -or -not $SigningPublicKey)) {
    throw "Non-development releases require -SigningPrivateKey and -SigningPublicKey"
}
if ($ReleaseIdentity.channel -ne "development" -and -not $CodeSigningCertificateThumbprint) {
    throw "Non-development Windows releases require -CodeSigningCertificateThumbprint"
}
$TrustedPublicKey = ""
if ($SigningPublicKey) {
    $TrustedPublicKey = (Get-Content (Resolve-Path $SigningPublicKey) -Raw).Trim()
}

if (-not $SkipWeb) {
    Push-Location (Join-Path $RepositoryRoot "apps\web")
    try {
        pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { throw "pnpm install failed" }
        pnpm run build
        if ($LASTEXITCODE -ne 0) { throw "web build failed" }
    }
    finally {
        Pop-Location
    }
}

$HostOutput = Join-Path $Output "build\local-drama-host.exe"
$LauncherOutput = Join-Path $Output "build\local-drama-launcher.exe"
New-Item -ItemType Directory -Force -Path (Split-Path $HostOutput) | Out-Null
Push-Location (Join-Path $RepositoryRoot "cmd\runtime-host")
try {
    go test ./...
    if ($LASTEXITCODE -ne 0) { throw "runtime host tests failed" }
    go build -trimpath -ldflags "-s -w -X main.hostVersion=$ReleaseVersion -X main.trustedReleasePublicKey=$TrustedPublicKey" -o $HostOutput .
    if ($LASTEXITCODE -ne 0) { throw "runtime host build failed" }
}
finally {
    Pop-Location
}

Push-Location (Join-Path $RepositoryRoot "cmd\desktop-launcher")
try {
    go test ./...
    if ($LASTEXITCODE -ne 0) { throw "desktop launcher tests failed" }
    go build -trimpath -ldflags "-s -w -H=windowsgui" -o $LauncherOutput .
    if ($LASTEXITCODE -ne 0) { throw "desktop launcher build failed" }
}
finally {
    Pop-Location
}

if ($CodeSigningCertificateThumbprint) {
    $SignTool = Get-Command signtool.exe -ErrorAction Stop
    foreach ($Binary in @($HostOutput, $LauncherOutput)) {
        & $SignTool.Source sign /sha1 $CodeSigningCertificateThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $Binary
        if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed: $Binary" }
    }
}

$BuildArgs = @(
    (Join-Path $RepositoryRoot "packaging\common\build_release.py"),
    "--platform", "windows-amd64",
    "--python-runtime", $PythonRuntime,
    "--ffmpeg-runtime", $FFmpegRuntime,
    "--host", $HostOutput,
    "--launcher", $LauncherOutput,
    "--output", $Output
)
if (-not $OnlineDependencies) {
    if (-not $Wheelhouse) { throw "Wheelhouse is required unless -OnlineDependencies is used" }
    $BuildArgs += @("--offline", "--wheelhouse", $Wheelhouse)
}
python @BuildArgs
if ($LASTEXITCODE -ne 0) { throw "release assembly failed" }

if ($SigningPrivateKey) {
    $ManifestPath = Join-Path $Output "LocalDramaStudio-$ReleaseVersion-windows-amd64\payload\release-manifest.json"
    Push-Location (Join-Path $RepositoryRoot "cmd\release-sign")
    try {
        go run . sign --manifest $ManifestPath --private (Resolve-Path $SigningPrivateKey).Path
        if ($LASTEXITCODE -ne 0) { throw "release manifest signing failed" }
    }
    finally {
        Pop-Location
    }
}

$PackageRoot = Join-Path $Output "LocalDramaStudio-$ReleaseVersion-windows-amd64"
$PortableArchive = Join-Path $Output "LocalDramaStudio-$ReleaseVersion-windows-amd64-portable.zip"
Compress-Archive -LiteralPath $PackageRoot -DestinationPath $PortableArchive -CompressionLevel Optimal -Force
Copy-Item -LiteralPath $PortableArchive -Destination (Join-Path $Output "LocalDramaStudio-$ReleaseVersion-windows-amd64.ldsupdate") -Force

if (-not $SkipInstaller) {
    $Compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if (-not $Compiler) {
        throw "Inno Setup compiler ISCC.exe was not found; use -SkipInstaller for a portable build"
    }
    & $Compiler.Source (Join-Path $PSScriptRoot "installer.iss") "/DReleaseOutput=$Output" "/DReleaseVersion=$ReleaseVersion"
    if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
    if ($CodeSigningCertificateThumbprint) {
        $InstallerPath = Join-Path $Output "installer\LocalDramaStudio-$ReleaseVersion-windows-x64-setup.exe"
        & $SignTool.Source sign /sha1 $CodeSigningCertificateThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $InstallerPath
        if ($LASTEXITCODE -ne 0) { throw "installer Authenticode signing failed" }
    }
}

$Checksums = @(Get-ChildItem -LiteralPath $Output -File; Get-ChildItem -LiteralPath (Join-Path $Output "installer") -File -ErrorAction SilentlyContinue) | Where-Object {
    $_.Extension -in @(".zip", ".ldsupdate", ".exe")
} | Sort-Object Name | ForEach-Object {
    $Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash.ToLowerInvariant()
    "$Hash  $($_.Name)"
}
[System.IO.File]::WriteAllLines(
    (Join-Path $Output "checksums.txt"),
    [string[]]$Checksums,
    (New-Object System.Text.UTF8Encoding($false))
)
