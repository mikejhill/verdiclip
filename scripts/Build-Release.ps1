#Requires -Version 5.1
<#
.SYNOPSIS
    Builds a distributable VerdiClip executable using PyInstaller.
.DESCRIPTION
    Creates a standalone .exe in the dist/ directory.
#>

[CmdletBinding()]
param(
    [switch]$OneFile,
    [switch]$Clean
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent

if ($Clean) {
    Write-Host "Cleaning previous builds..." -ForegroundColor Yellow
    $cleanPaths = @("$ProjectRoot\build", "$ProjectRoot\dist", "$ProjectRoot\*.spec")
    foreach ($p in $cleanPaths) {
        if (Test-Path $p) {
            Remove-Item $p -Recurse -Force
            Write-Host "  Removed: $p"
        }
    }
}

# Use uvx to run PyInstaller in an isolated environment so it doesn't pollute
# the project's venv (uv sync would remove it as an unmanaged dependency).
Write-Host "`nBuilding with PyInstaller (via uvx)..." -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    $pyinstallerArgs = @(
        "src\verdiclip\__main__.py",
        "--name", "VerdiClip",
        "--windowed",
        "--noconfirm",
        "--clean"
    )

    if (Test-Path "resources\icons\verdiclip.ico") {
        $pyinstallerArgs += @("--icon", "resources\icons\verdiclip.ico")
    }

    if ($OneFile) {
        $pyinstallerArgs += "--onefile"
    }

    # Add hidden imports for PySide6
    $hiddenImports = @(
        "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets",
        "PySide6.QtPrintSupport", "mss", "pynput", "PIL"
    )
    foreach ($imp in $hiddenImports) {
        $pyinstallerArgs += @("--hidden-import", $imp)
    }

    Write-Host "`nBuilding VerdiClip..." -ForegroundColor Cyan
    & uvx pyinstaller @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

    $outputDir = if ($OneFile) { "dist\VerdiClip.exe" } else { "dist\VerdiClip\" }
    Write-Host "`n========================================" -ForegroundColor Green
    Write-Host "  Build successful!" -ForegroundColor Green
    Write-Host "  Output: $ProjectRoot\$outputDir" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
} finally {
    Pop-Location
}
