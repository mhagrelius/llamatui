# Whisper STT runs as a separately-built CUDA service the TUI discovers or spawns

**Status:** accepted

For local voice dictation we run whisper.cpp `whisper-server` as a **separate process with
its own CUDA 12.8 build, isolated in a `whisper/` subdir**, that the TUI **discovers if already
running and otherwise lazy-spawns** (`WhisperServer.ensure_running()` reuses an answering server,
else spawns; `close()` kills only a subprocess it spawned). We chose this over the two obvious
alternatives — a CPU build, and an unconditional TUI-owned subprocess — for specific reasons.

## Why not CPU

The owner dictates a paragraph at a time and prefers spending ~0.5 GB of VRAM (32 GB available,
llama context trimmable toward ~10k to make room) over CPU transcription latency. A CUDA build
keeps transcription near-instant even while a reply is streaming on the same GPU.

## Why a *separate* build, not "reusing the local ggml/CUDA stack"

Prebuilt whisper.cpp ships its **own** ggml/cuBLAS DLLs that may differ from the repo-root
llama.cpp build. They are not shared. whisper-server, its DLLs, and the model live in a dedicated
`whisper/` subdir and the process is spawned with `cwd` set there so its DLLs resolve locally; the
repo-root llama stack is never touched. (The original design note's "reusing the local ggml/CUDA
stack" was wrong and is corrected by this ADR.)

## Why discover-then-spawn, not unconditional spawn

llama-server is already an always-on shared resource that multiple tools hit. Whisper may become
the same. Making `ensure_running()` reuse an external server when one answers — and `close()`
terminate **only** what this instance spawned — gives the streamlined single-command experience by
default *and* the shared-server pattern for free, with no interface change. This mirrors how
`client.py` treats the llama-server URL as an external endpoint it does not own.

## Consequences

- A `--whisper-url` config lets the TUI point at an external server; otherwise it spawns one lazily.
- The TUI must never kill a server it merely connected to (own-only-what-you-spawned).
- Setup ships a CUDA whisper release + cuBLAS DLLs, version-matched to the user's driver — a heavier
  `scripts/get-whisper.ps1` than a CPU build would need.
