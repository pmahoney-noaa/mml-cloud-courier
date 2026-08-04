# Downloads the fake-gcs-server emulator binary into tools/fake-gcs-server.exe.
# Uses the GitHub API to find the Windows amd64 asset so release naming drift
# does not break us. Run once per machine:
#   pwsh tests/tools/get-fake-gcs-server.ps1
param([string]$Version = "latest")

$ErrorActionPreference = "Stop"
$toolsDir = Join-Path $PSScriptRoot "..\..\tools"
New-Item -ItemType Directory -Force $toolsDir | Out-Null
$exePath = Join-Path $toolsDir "fake-gcs-server.exe"

$api = if ($Version -eq "latest") {
    "https://api.github.com/repos/fsouza/fake-gcs-server/releases/latest"
} else {
    "https://api.github.com/repos/fsouza/fake-gcs-server/releases/tags/v$Version"
}
$release = Invoke-RestMethod -Uri $api
$asset = $release.assets | Where-Object { $_.name -match "Windows" -and $_.name -match "amd64" } | Select-Object -First 1
if (-not $asset) { throw "No Windows amd64 asset in release $($release.tag_name)" }

$archive = Join-Path $env:TEMP $asset.name
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive
$extractDir = Join-Path $env:TEMP "fake-gcs-server-extract"
if (Test-Path $extractDir) { Remove-Item -Recurse -Force $extractDir }
New-Item -ItemType Directory -Force $extractDir | Out-Null

if ($asset.name -like "*.zip") {
    Expand-Archive -Path $archive -DestinationPath $extractDir
} else {
    tar -xzf $archive -C $extractDir
}
$exe = Get-ChildItem -Recurse $extractDir -Filter "fake-gcs-server*.exe" | Select-Object -First 1
if (-not $exe) { throw "No exe found inside $($asset.name)" }
Copy-Item $exe.FullName $exePath -Force
Write-Host "Installed $($release.tag_name) -> $exePath"
