#Requires -Version 5.1
<#
.SYNOPSIS
    Sets up the VerdiClip development environment.
.DESCRIPTION
    Verifies prerequisites (Python 3.12+, uv), installs dependencies,
    and verifies the installation.
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-PythonVersion {
    try {
        $version = & python --version 2>&1
        if ($version -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -ge 3 -and $minor -ge 12) {
                Write-Host "[OK] Python $major.$minor found." -ForegroundColor Green
                return $true
            }
        }
    } catch {}
    Write-Host "[ERROR] Python 3.12+ is required." -ForegroundColor Red
    return $false
}

function Test-UvInstalled {
    try {
        $null = & uv --version 2>&1
        Write-Host "[OK] uv is installed." -ForegroundColor Green
        return $true
    } catch {
        Write-Host "[WARN] uv not found. Installing via official installer..." -ForegroundColor Yellow
        try {
            & powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
            if ($LASTEXITCODE -ne 0) { throw "Installer exited with code $LASTEXITCODE" }
            # Refresh PATH so uv is available in the current session
            $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
            Write-Host "[OK] uv installed via official installer." -ForegroundColor Green
            return $true
        } catch {
            Write-Host "[ERROR] Failed to install uv. Visit https://docs.astral.sh/uv/getting-started/installation/" -ForegroundColor Red
            return $false
        }
    }
}

function Install-Dependencies {
    Write-Host "`nInstalling dependencies..." -ForegroundColor Cyan
    Push-Location $PSScriptRoot\..
    try {
        & uv sync --all-extras
        if ($LASTEXITCODE -ne 0) {
            throw "uv sync failed with exit code $LASTEXITCODE"
        }
        Write-Host "[OK] Dependencies installed." -ForegroundColor Green
    } finally {
        Pop-Location
    }
}

function Test-Installation {
    Write-Host "`nVerifying installation..." -ForegroundColor Cyan
    Push-Location $PSScriptRoot\..
    try {
        & uv run python -c "import verdiclip; print(f'VerdiClip v{verdiclip.__version__} loaded.')"
        if ($LASTEXITCODE -ne 0) {
            throw "Import verification failed."
        }
        Write-Host "[OK] Installation verified." -ForegroundColor Green
    } finally {
        Pop-Location
    }
}

# Main
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  VerdiClip Development Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Test-PythonVersion)) { exit 1 }
if (-not (Test-UvInstalled)) { exit 1 }
Install-Dependencies
Test-Installation

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Run VerdiClip:   uv run verdiclip"
Write-Host "Run tests:       uv run pytest"
Write-Host "Run linter:      uv run ruff check src/"
Write-Host "Type check:      uv run ty check src/"
Write-Host ""
