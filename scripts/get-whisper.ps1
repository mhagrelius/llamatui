# Downloads whisper.cpp's prebuilt Windows CUDA whisper-server (+ its bundled cuBLAS/ggml DLLs)
# and the small.en model into ./whisper/. The newest prebuilt CUDA build is 12.4 — there is no
# 12.8 prebuilt, but CUDA 12.x is forward-compatible, so the 12.4 binaries run on a 12.8 driver.
# Bump $WhisperVersion / $zipName as new releases land (see the releases page for asset names).
param(
    [string]$WhisperVersion = "v1.9.1",
    [string]$ModelUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
)

$ErrorActionPreference = "Stop"
$dir = Join-Path $PSScriptRoot "../whisper"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# 1) whisper-server CUDA release (zip with whisper-server.exe + DLLs).
# Asset list for a release: https://github.com/ggml-org/whisper.cpp/releases
$zipName = "whisper-cublas-12.4.0-bin-x64.zip"   # CUDA 12.4 x64 build; adjust per chosen release
$releaseUrl = "https://github.com/ggml-org/whisper.cpp/releases/download/$WhisperVersion/$zipName"
$zipPath = Join-Path $dir $zipName
Write-Host "Downloading whisper-server CUDA build: $releaseUrl"
Invoke-WebRequest -Uri $releaseUrl -OutFile $zipPath
Expand-Archive -Path $zipPath -DestinationPath $dir -Force
Remove-Item $zipPath

# The CUDA zips nest everything under a Release/ folder; flatten it so whisper-server.exe, its
# DLLs, and the model all sit directly in whisper/ (the default --whisper-bin location).
$nested = Join-Path $dir "Release"
if (Test-Path $nested) {
    Get-ChildItem -Path $nested -Force | Move-Item -Destination $dir -Force
    Remove-Item $nested -Recurse -Force
}

# Sanity-check the server binary name (older zips shipped "server.exe", newer "whisper-server.exe").
$serverExe = Get-ChildItem -Path $dir -Filter "*server*.exe" -Recurse | Select-Object -First 1
if ($serverExe) {
    Write-Host "Found server binary: $($serverExe.Name)"
    if ($serverExe.Name -ne "whisper-server.exe") {
        Write-Host "  NOTE: not named whisper-server.exe — pass it via --whisper-bin `"$($serverExe.FullName)`""
    }
} else {
    Write-Host "  WARNING: no *server*.exe found in the extracted archive — check the zip contents."
}

# 2) Model.
$modelPath = Join-Path $dir "ggml-small.en.bin"
if (-not (Test-Path $modelPath)) {
    Write-Host "Downloading model: $ModelUrl"
    Invoke-WebRequest -Uri $ModelUrl -OutFile $modelPath
}

Write-Host "Done. whisper/ now holds whisper-server.exe, its DLLs, and ggml-small.en.bin."
Write-Host "Enable the capture extra with:  uv sync --extra voice"
