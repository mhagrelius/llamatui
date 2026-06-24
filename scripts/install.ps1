# One-shot onboarding: installs llamatui (with voice + semantic extras) as a global `llamatui`
# command, then fetches the whisper runtime into the per-user data dir. Run from anywhere in the
# repo: .\scripts\install.ps1   (add -SkipVoice to skip the ~500 MB whisper download)
param([switch]$SkipVoice)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent      # repo root (this script lives in scripts/)

Push-Location $root
try {
    Write-Host "Installing llamatui with voice + semantic extras..."
    uv tool install --force ".[voice,semantic]"
    if (-not $SkipVoice) {
        Write-Host "Fetching whisper-server + model (~500 MB) into the user-data dir..."
        uv run python -m llamatui --setup-voice   # PATH-independent: runs via uv in the repo .venv, no need for llamatui to be on PATH yet
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Done. Start your llama-server, then run from any directory:  llamatui"
if ($SkipVoice) {
    Write-Host "Voice skipped — enable it later with:  llamatui --setup-voice"
}
