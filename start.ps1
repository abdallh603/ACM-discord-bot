$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$botPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $botPython)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    $runtimePython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if ($pythonCommand) {
        $setupPython = $pythonCommand.Source
    } elseif (Test-Path -LiteralPath $runtimePython) {
        $setupPython = $runtimePython
    } else {
        throw 'Install Python 3.11 or newer, then run this launcher again.'
    }
    & $setupPython -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}

& $botPython -c "import importlib.util, sys; sys.exit(0 if all(importlib.util.find_spec(name) is not None for name in ('discord', 'dotenv', 'tzdata', 'PIL')) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing bot dependencies...'
    & $botPython -m pip install --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your internet connection and retry.' }
}

if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    throw 'Enter your bot token and server ID in the new .env file, then run this launcher again.'
}

Write-Host 'Checking the bot before connecting...'
& $botPython -m unittest discover
if ($LASTEXITCODE -ne 0) { throw 'The checks failed. The bot has not been started.' }

Write-Host 'Starting RoundForge. Keep this window open; press Ctrl+C to stop.'
& $botPython -m just_bot
if ($LASTEXITCODE -ne 0) { throw 'The bot exited with an error. See the message above.' }
