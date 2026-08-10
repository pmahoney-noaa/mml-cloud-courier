#Requires -Version 7
<#
Build the MML Cloud Courier release: PyInstaller onedir -> Inno setup.exe.
Run from anywhere; paths resolve relative to this script's repo.

    pwsh packaging/build_release.ps1                 # full build
    pwsh packaging/build_release.ps1 -SkipInstaller  # bundle only
#>
param(
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "venv python not found at $python" }

$version = & $python -c "import tomllib, pathlib; print(tomllib.loads(pathlib.Path(r'$root\pyproject.toml').read_text('utf-8'))['project']['version'])"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($version)) { throw "failed to read [project].version from pyproject.toml" }
Write-Host "Building MML Cloud Courier $version"

& $python (Join-Path $root "packaging\version_info.py")
if ($LASTEXITCODE -ne 0) { throw "version_info.py failed" }

& $python -m PyInstaller (Join-Path $root "packaging\mmlcc.spec") `
    --noconfirm --distpath (Join-Path $root "dist") `
    --workpath (Join-Path $root "build\pyinstaller")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

if (-not $SkipInstaller) {
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) {
        $iscc = "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    }
    if (-not (Test-Path $iscc)) {
        throw "ISCC.exe not found - install Inno Setup 6 (winget install -e --id JRSoftware.InnoSetup)"
    }
    & $iscc "/DAppVersion=$version" `
        "/DDistDir=$root\dist\mml-cloud-courier" `
        "/O$root\dist" `
        (Join-Path $root "packaging\mmlcc.iss")
    if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
    Write-Host "Installer: dist\mml-cloud-courier-setup-$version.exe"
}
