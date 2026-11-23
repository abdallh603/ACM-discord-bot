param([string]$ClientId = '', [int]$Port = 8080, [switch]$BuildOnly)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Port must be between 1 and 65535.' }
Set-Location -LiteralPath $PSScriptRoot
$activityPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $activityPython)) { throw 'Install the Python requirements first; see README.md.' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22 or newer first.' }
Push-Location -LiteralPath (Join-Path $PSScriptRoot 'activity-client')
try {
    if (Test-Path -LiteralPath 'package-lock.json') { & npm.cmd ci } else { & npm.cmd install }
    if ($LASTEXITCODE -ne 0) { throw 'SDK dependency download failed. Run this in a terminal with internet access.' }
    & npm.cmd run build:sdk
    if ($LASTEXITCODE -ne 0) { throw 'The SDK build failed; inspect the build message above.' }
} finally { Pop-Location }
if (-not $BuildOnly) {
    $setupArguments = @('-m', 'just_bot.activity_setup', '--configure', '--port', $Port)
    if ($ClientId) { $setupArguments += @('--client-id', $ClientId) }
    & $activityPython @setupArguments
    if ($LASTEXITCODE -ne 0) { throw 'Local setup is incomplete; follow the checklist above.' }
}
Write-Host 'SDK build completed. Follow ACTIVITY.md for the HTTPS host, URL Mapping, and live Discord check.'
