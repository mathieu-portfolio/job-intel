# Build the Windows desktop distribution bundle for Job Intel.
# Run from anywhere inside the repository with an activated virtual environment:
#   .\scripts\build_windows.ps1

[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$NoClean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$SpecPath = Join-Path $ProjectRoot "packaging\pyinstaller\job_intel_desktop.spec"
$DistDir = Join-Path $ProjectRoot "dist"
$ReleaseDir = Join-Path $DistDir "releases"
$BundleDir = Join-Path $DistDir "JobIntel"
$ReleaseZip = Join-Path $ReleaseDir "JobIntel-Windows.zip"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PythonExe = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

if (-not (Test-Path $SpecPath)) {
    throw "PyInstaller spec not found: $SpecPath"
}

Push-Location $ProjectRoot
try {
    if (-not $SkipInstall) {
        Write-Host "Installing/updating project dependencies..."
        & $PythonExe -m pip install -e .
        Write-Host "Installing/updating PyInstaller..."
        & $PythonExe -m pip install pyinstaller
    }

    $pyinstallerArgs = @("-m", "PyInstaller", $SpecPath, "--noconfirm")
    if (-not $NoClean) {
        $pyinstallerArgs += "--clean"
    }

    Write-Host "Building JobIntel desktop bundle..."
    & $PythonExe @pyinstallerArgs

    if (-not (Test-Path $BundleDir)) {
        throw "Expected bundle was not generated: $BundleDir"
    }

    New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
    if (Test-Path $ReleaseZip) {
        Remove-Item $ReleaseZip -Force
    }

    Write-Host "Creating release archive..."
    Compress-Archive -Path $BundleDir -DestinationPath $ReleaseZip -Force

    Write-Host ""
    Write-Host "Build complete."
    Write-Host "Bundle:  $BundleDir"
    Write-Host "Release: $ReleaseZip"
}
finally {
    Pop-Location
}
