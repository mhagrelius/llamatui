# Onboarding: installable, run-from-anywhere llamatui — design

**Status:** approved (design), pending implementation plan
**Date:** 2026-06-24

## Goal

Install `llamatui` once and run it from any directory with a bare `llamatui`
command, getting both optional extras (`voice`, `semantic`) and the whisper
assets in a single setup step. Today the app runs as `python -m llamatui` from
the repo, the optional extras are installed by hand, and the whisper binary is
discovered **relative to the current directory** — so a globally-installed
command launched elsewhere can't find it.

## Decisions (locked)

| Question | Decision |
|---|---|
| Install mechanism | **`uv tool install ".[voice,semantic]"`** — isolated env, `llamatui` shim on PATH, both extras in one command |
| Where whisper assets live | **`platformdirs.user_data_dir("llamatui")/whisper/`** (same root as the conversations DB), with a **`./whisper` dev fallback** |
| Override knobs | No new env var — existing `--whisper-bin` / `--whisper-model` flags plus the stable default are enough |
| One-shot setup | **`scripts/install.ps1`** orchestrates tool-install + asset-fetch (`-SkipVoice` to skip the ~500 MB download) |
| Asset fetch delivery | **Built into the app as `llamatui --setup-voice`** (Python download/extract/flatten); `scripts/get-whisper.ps1` is retired |

## Non-goals (YAGNI)

- No `--whisper-dir` flag or `LLAMATUI_WHISPER_DIR` env var — the user-data
  default plus `--whisper-bin`/`--whisper-model` cover every case.
- No cross-platform install story — Windows + the CUDA whisper build only, as
  the rest of the app already assumes.
- No auto-update of the model or binary — `--setup-voice` re-run handles refresh;
  `uv tool upgrade llamatui` handles the code.
- No publishing to PyPI — install is from the local checkout for now.
- No bundling the ~500 MB model inside the wheel (it would bloat every install
  and reinstall); assets live in the user-data dir, decoupled from the code.

## Architecture

Four small, well-bounded units plus targeted edits. Each has one clear
responsibility and a narrow interface, consistent with `CONTEXT.md`.

### New: `llamatui/paths.py` — where llamatui keeps things

The single source of truth for per-user locations.

- `user_data_dir() -> Path` — `platformdirs.user_data_dir("llamatui", appauthor=False)`.
- `default_whisper_dir() -> Path` — `user_data_dir() / "whisper"`.

`storage.py` is edited to reuse `paths.user_data_dir()` in `default_db_path()`
so the DB and the whisper assets share one root and the platformdirs call lives
in exactly one place.

### New: `llamatui/setup_voice.py` — fetch the whisper assets

Owns *downloading and laying out the whisper runtime*. Nothing else knows the
release URLs or the zip layout.

- `fetch_whisper(dest: Path, *, download=<default httpx streamer>) -> None`:
  1. `mkdir -p dest`.
  2. Download the whisper.cpp **Windows CUDA** release zip
     (`whisper-cublas-12.4.0-bin-x64.zip` from the `ggml-org/whisper.cpp`
     `v1.9.1` release) to a temp file.
  3. Extract with `zipfile`; if everything is nested under a `Release/` folder,
     **flatten** it so `whisper-server.exe` + DLLs sit directly in `dest`.
  4. Download `ggml-small.en.bin` from Hugging Face into `dest` (skip if present).
  5. Verify `whisper-server.exe` exists in `dest`; print a one-line status.
- The actual byte transfer is the injected `download(url, path)` seam, so tests
  serve a synthetic zip and a tiny model file — **no real network**.

**Concrete target (locked):** `--setup-voice` always passes `default_whisper_dir()`,
which on Windows resolves to `%LOCALAPPDATA%\llamatui\whisper\` (e.g.
`C:\Users\<you>\AppData\Local\llamatui\whisper\`). `whisper-server.exe`, its DLLs,
and `ggml-small.en.bin` all land **directly in that folder** — never the user home
root, never the repo root. This is the same `…\AppData\Local\llamatui\` root the
conversations DB already uses. A test asserts the extracted `whisper-server.exe`
path is under `default_whisper_dir()`.

The release version, asset name, and model URL are module-level constants (the
same values verified live against the real binary on 2026-06-24).

### New: `scripts/install.ps1` — the one-shot bootstrap

```powershell
param([switch]$SkipVoice)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent     # repo root (script lives in scripts/)
Push-Location $root
try {
    Write-Host "Installing llamatui with voice + semantic extras..."
    uv tool install --force ".[voice,semantic]"
    if (-not $SkipVoice) {
        Write-Host "Fetching whisper-server + model (~500 MB)..."
        uv run python -m llamatui --setup-voice    # PATH-independent: uses the project env
    }
}
finally { Pop-Location }
Write-Host "Done. Start llama-server, then run:  llamatui"
```

Fetching via `uv run python -m llamatui --setup-voice` (the project env) instead
of the freshly-installed shim avoids a same-shell PATH-refresh race.

### Edits

- **`llamatui/app.py`**
  - `resolve_whisper_dir() -> Path` (module-level): return `Path("whisper")` when
    `whisper/whisper-server.exe` exists there (dev), else `default_whisper_dir()`.
  - Construct `WhisperServer(whisper_dir=resolve_whisper_dir(), bin_path=…, …)`.
    Explicit `--whisper-bin`/`--whisper-model` still override.
  - The voice-off hint text changes to **"voice off — run `llamatui --setup-voice`"**.
- **`llamatui/__main__.py`**
  - Add `--setup-voice` (`action="store_true"`). When set, call
    `setup_voice.fetch_whisper(paths.default_whisper_dir())` and **return** before
    constructing `Config`/launching the TUI.
- **`scripts/get-whisper.ps1`** — **removed**.
- **`README.md`** — onboarding section rewritten (see below).
- **`CONTEXT.md`** — one glossary line noting `paths` as the owner of per-user
  locations.

`WhisperServer` itself is unchanged: it still resolves a binary/model within the
single directory it is handed.

## Onboarding flow (end state)

```
git clone … ; cd llama
.\scripts\install.ps1               # tool + extras + whisper assets   (-SkipVoice to skip)
llamatui                            # from ANY directory
```

Update later: `uv tool upgrade llamatui` (code), re-run `llamatui --setup-voice`
(assets). Enable voice after a `-SkipVoice` install: `llamatui --setup-voice`.

## Data flow

```
install.ps1
  → uv tool install ".[voice,semantic]"        # llamatui shim on PATH
  → uv run python -m llamatui --setup-voice     # unless -SkipVoice
        → setup_voice.fetch_whisper(default_whisper_dir())
              download CUDA zip → extract → flatten Release/ → download model → verify

runtime:  llamatui (any cwd)
  → app.resolve_whisper_dir()  → ./whisper (dev) | default_whisper_dir()
  → WhisperServer(whisper_dir=…)  → finds whisper-server.exe + model  → voice on
  (assets missing → voice off, hint: "run llamatui --setup-voice")
```

## Error handling

| Failure | Behavior |
|---|---|
| `uv` not installed | `install.ps1` stops with a clear message (install uv first) |
| download fails mid-fetch | `fetch_whisper` raises with the URL; partial model file removed; re-run resumes (skip-if-present keeps a completed model) |
| zip has no `whisper-server.exe` after extract | `fetch_whisper` raises (bad/!changed asset) |
| assets missing at runtime | existing graceful degrade: voice **off**, hint "run `llamatui --setup-voice`" |
| run from a dir with a stale `./whisper` | dev fallback only triggers if `whisper-server.exe` is actually present there |

## Testing

Interface = test surface. No real network or subprocess.

- **`tests/test_paths.py`**: `default_whisper_dir()` is under `user_data_dir()`
  and ends with `whisper`; `storage.default_db_path()` shares the same root.
- **`tests/test_app_resolve.py`** (or fold into an existing app test): pure
  `resolve_whisper_dir(cwd_dir)` returns the dev dir when it contains
  `whisper-server.exe`, else `default_whisper_dir()`. (Make `resolve_whisper_dir`
  take an optional base path so it's testable without changing the process cwd.)
- **`tests/test_setup_voice.py`**: `fetch_whisper(dest, download=fake)` where the
  fake writes a synthetic zip nesting `Release/whisper-server.exe` + a dummy DLL,
  and a tiny model file → assert the binary + DLL land flattened in `dest`, the
  model is written, a present model is **not** re-downloaded, and a zip lacking
  the server binary raises.
- `install.ps1` parse-checks (PowerShell AST), as `get-whisper.ps1` did.

## Files touched / added

- **add** `llamatui/paths.py`
- **add** `llamatui/setup_voice.py`
- **add** `scripts/install.ps1`
- **add** `tests/test_paths.py`, `tests/test_setup_voice.py`, `tests/test_app_resolve.py`
- **remove** `scripts/get-whisper.ps1`
- **edit** `llamatui/app.py` — `resolve_whisper_dir`, `WhisperServer` construction, hint text
- **edit** `llamatui/__main__.py` — `--setup-voice` flag
- **edit** `llamatui/storage.py` — reuse `paths.user_data_dir()`
- **edit** `README.md` — onboarding
- **edit** `CONTEXT.md` — `paths` glossary line
