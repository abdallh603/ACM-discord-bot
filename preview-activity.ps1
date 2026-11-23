param([int]$Port = 8080)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$activityPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $activityPython)) {
    throw 'Create the Python environment and install requirements.txt first; see README.md.'
}
Write-Host "Opening the local review server at http://127.0.0.1:$Port (fictional players; no Discord credentials)."
& $activityPython -m just_bot.activity_server --preview --port $Port
