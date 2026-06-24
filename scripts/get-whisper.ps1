# Downloads whisper.cpp's prebuilt Windows CUDA whisper-server (+ its bundled cuBLAS/ggml DLLs)
# and the small.en model into ./whisper/. Match the CUDA build to your driver — these defaults
# target the CUDA 12.x prebuilt release. Bump the version/URLs as new releases land.
param(
    [string]$WhisperVersion = "v1.7.4",
    [string]$ModelUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
)

$ErrorActionPreference = "Stop"
$dir = Join-Path $PSScriptRoot "..\whisper"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# 1) whisper-server CUDA release (zip with whisper-server.exe + DLLs).
$zipName = "whisper-cublas-12.4.0-bin-x64.zip"   # adjust to the asset name on the chosen release
$releaseUrl = "https://github.com/ggerganov/whisper.cpp/releases/download/$WhisperVersion/$zipName"
$zipPath = Join-Path $dir $zipName
Write-Host "Downloading whisper-server CUDA build: $releaseUrl"
Invoke-WebRequest -Uri $releaseUrl -OutFile $zipPath
Expand-Archive -Path $zipPath -DestinationPath $dir -Force
Remove-Item $zipPath

# 2) Model.
$modelPath = Join-Path $dir "ggml-small.en.bin"
if (-not (Test-Path $modelPath)) {
    Write-Host "Downloading model: $ModelUrl"
    Invoke-WebRequest -Uri $ModelUrl -OutFile $modelPath
}

Write-Host "Done. whisper/ now holds whisper-server.exe, its DLLs, and ggml-small.en.bin."
Write-Host "Enable the capture extra with:  uv sync --extra voice"
