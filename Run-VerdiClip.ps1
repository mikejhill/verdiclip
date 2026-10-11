# Start the tray app without waiting or leaving a console attached.
$launcher = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    Write-Error 'VerdiClip is not installed. Run "uv sync" in this directory first.'
    exit 1
}
Start-Process -FilePath $launcher -ArgumentList '-m', 'verdiclip' -WorkingDirectory $PSScriptRoot
