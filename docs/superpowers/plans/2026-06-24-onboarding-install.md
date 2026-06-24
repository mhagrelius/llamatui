# Onboarding / Installable llamatui Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `llamatui` install once and run from any directory, with both extras and the whisper assets set up in one step, by giving whisper a fixed per-user home and a built-in `--setup-voice` fetch.

**Architecture:** A new `paths.py` owns per-user on-disk locations (the DB and whisper assets share one root). A new `setup_voice.py` downloads/extracts/flattens whisper-server + the model into a target dir behind an injected download seam. `app.py` resolves the whisper dir as `./whisper` (dev) else the user-data dir. `__main__.py` gains a `--setup-voice` flag. `scripts/install.ps1` orchestrates `uv tool install` + the fetch; `scripts/get-whisper.ps1` is retired.

**Tech Stack:** Python 3.11+, platformdirs, httpx, zipfile (stdlib), uv, PowerShell, pytest.

Design: [docs/superpowers/specs/2026-06-24-onboarding-install-design.md](docs/superpowers/specs/2026-06-24-onboarding-install-design.md).

## Global Constraints

- `requires-python = ">=3.11"`; new modules start with `from __future__ import annotations`.
- No real network or subprocess in tests — the download is an injected seam; tests use a synthetic zip (mirror the fake-seam style in `tests/test_whisper.py`).
- No new base dependencies — `httpx` and `platformdirs` are already declared.
- Whisper assets install target is `platformdirs.user_data_dir("llamatui", appauthor=False) / "whisper"` (= `%LOCALAPPDATA%\llamatui\whisper\` on Windows) — never the home root or repo root.
- `user_data_dir()` MUST use `appauthor=False` so it matches the existing DB location.
- Windows + the CUDA whisper build only (the rest of the app already assumes this).
- Retire `scripts/get-whisper.ps1`; the fetch lives in `llamatui --setup-voice`.
- Run tests with `C:\llama\.venv\Scripts\python.exe -m pytest`. Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: `paths.py` — per-user locations (+ `storage` reuse)

**Files:**
- Create: `llamatui/paths.py`
- Modify: `llamatui/storage.py` (`default_db_path`, imports)
- Test: `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `paths.user_data_dir() -> Path`, `paths.default_whisper_dir() -> Path`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_paths.py`:

```python
"""paths owns per-user on-disk locations; the DB and whisper assets share one root."""

from llamatui import paths, storage


def test_user_data_dir_is_absolute():
    assert paths.user_data_dir().is_absolute()


def test_default_whisper_dir_under_user_data_dir():
    assert paths.default_whisper_dir().parent == paths.user_data_dir()
    assert paths.default_whisper_dir().name == "whisper"


def test_db_and_whisper_share_root():
    assert storage.default_db_path().parent == paths.user_data_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest tests/test_paths.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'llamatui.paths'`

- [ ] **Step 3: Create `llamatui/paths.py`**

```python
"""Where llamatui keeps per-user data on disk.

One place so the conversations DB and the whisper assets share a single root, independent of
the current working directory.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs


def user_data_dir() -> Path:
    """The per-user data root, e.g. ``%LOCALAPPDATA%\\llamatui`` on Windows."""
    return Path(platformdirs.user_data_dir("llamatui", appauthor=False))


def default_whisper_dir() -> Path:
    """Where ``llamatui --setup-voice`` installs whisper-server + the model."""
    return user_data_dir() / "whisper"
```

- [ ] **Step 4: Edit `llamatui/storage.py` to reuse `paths.user_data_dir()`**

Replace the `import platformdirs` line with `from . import paths`, and change `default_db_path`:

```python
def default_db_path() -> Path:
    d = paths.user_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "conversations.db"
```

(Confirm `platformdirs` is no longer referenced elsewhere in `storage.py` before removing its import.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest tests/test_paths.py -q`
Expected: PASS (3 tests)

- [ ] **Step 6: Run the full suite (storage import changed)**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS — no regressions (DB location is unchanged because `appauthor=False` is preserved).

- [ ] **Step 7: Commit**

```bash
git add llamatui/paths.py llamatui/storage.py tests/test_paths.py
git commit -m "feat: add paths module for per-user locations; storage reuses it"
```

---

### Task 2: `setup_voice.py` — fetch whisper-server + model

**Files:**
- Create: `llamatui/setup_voice.py`
- Test: `tests/test_setup_voice.py`

**Interfaces:**
- Consumes: nothing (takes its destination as an argument).
- Produces: `setup_voice.fetch_whisper(dest: Path, *, download=_http_download) -> Path` (returns the server exe path); module constants `WHISPER_RELEASE_URL`, `MODEL_URL`, `MODEL_NAME`, `SERVER_EXE`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_setup_voice.py`:

```python
"""fetch_whisper lays out whisper-server + model into a dir; the download is an injected
seam so no real network is touched (synthetic zip, like the fakes in test_whisper.py)."""

import io
import zipfile
from pathlib import Path

import pytest

from llamatui.setup_voice import (
    fetch_whisper, WHISPER_RELEASE_URL, MODEL_URL, MODEL_NAME, SERVER_EXE,
)


def _zip_bytes(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n in names:
            z.writestr(n, b"x")
    return buf.getvalue()


def _make_download(zip_payload):
    calls = []

    def download(url, dest):
        calls.append(url)
        Path(dest).write_bytes(zip_payload if url == WHISPER_RELEASE_URL else b"MODELDATA")

    download.calls = calls
    return download


def test_fetch_flattens_release_and_downloads_model(tmp_path):
    dl = _make_download(_zip_bytes(["Release/whisper-server.exe", "Release/ggml-cuda.dll"]))
    exe = fetch_whisper(tmp_path, download=dl)
    assert exe == tmp_path / SERVER_EXE
    assert (tmp_path / SERVER_EXE).exists()
    assert (tmp_path / "ggml-cuda.dll").exists()      # flattened out of Release/
    assert not (tmp_path / "Release").exists()
    assert (tmp_path / MODEL_NAME).read_bytes() == b"MODELDATA"
    assert MODEL_URL in dl.calls


def test_zip_without_release_dir_also_works(tmp_path):
    dl = _make_download(_zip_bytes(["whisper-server.exe", "ggml.dll"]))
    exe = fetch_whisper(tmp_path, download=dl)
    assert exe.exists()
    assert (tmp_path / "ggml.dll").exists()


def test_existing_model_is_not_redownloaded(tmp_path):
    (tmp_path / MODEL_NAME).write_bytes(b"ALREADY")
    dl = _make_download(_zip_bytes(["Release/whisper-server.exe"]))
    fetch_whisper(tmp_path, download=dl)
    assert (tmp_path / MODEL_NAME).read_bytes() == b"ALREADY"
    assert MODEL_URL not in dl.calls


def test_missing_server_binary_raises(tmp_path):
    dl = _make_download(_zip_bytes(["Release/not-the-server.exe"]))
    with pytest.raises(RuntimeError, match="whisper-server.exe"):
        fetch_whisper(tmp_path, download=dl)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest tests/test_setup_voice.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'llamatui.setup_voice'`

- [ ] **Step 3: Create `llamatui/setup_voice.py`**

```python
"""Fetch the whisper.cpp runtime (CUDA whisper-server + the small.en model) into a target dir.

Owns the release URLs and the on-disk layout; nothing else knows them. The byte transfer is an
injected seam so tests use a synthetic zip and never hit the network. Values verified live
against the real binary on 2026-06-24 (RTX 5090 / Blackwell; the CUDA 12.4 build PTX-JITs to it).
"""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

import httpx

WHISPER_VERSION = "v1.9.1"
WHISPER_ZIP = "whisper-cublas-12.4.0-bin-x64.zip"
WHISPER_RELEASE_URL = (
    f"https://github.com/ggml-org/whisper.cpp/releases/download/{WHISPER_VERSION}/{WHISPER_ZIP}"
)
MODEL_NAME = "ggml-small.en.bin"
MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{MODEL_NAME}"
SERVER_EXE = "whisper-server.exe"


def _http_download(url: str, dest: Path) -> None:
    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)


def fetch_whisper(dest: Path, *, download: Callable[[str, Path], None] = _http_download) -> Path:
    """Download + lay out whisper-server and the model into ``dest``. Returns the server exe path.

    The CUDA zips nest everything under ``Release/``; this flattens it so the exe + DLLs sit
    directly in ``dest`` (the default ``--whisper-bin`` location). A present model is not
    re-downloaded. Raises if the server binary is missing after extraction.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / WHISPER_ZIP
        download(WHISPER_RELEASE_URL, zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(dest)

    nested = dest / "Release"
    if nested.is_dir():
        for item in nested.iterdir():
            target = dest / item.name
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            shutil.move(str(item), str(dest))
        nested.rmdir()

    exe = dest / SERVER_EXE
    if not exe.exists():
        raise RuntimeError(f"{SERVER_EXE} not found after extracting {WHISPER_ZIP} into {dest}")

    model = dest / MODEL_NAME
    if not model.exists():
        download(MODEL_URL, model)

    return exe
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest tests/test_setup_voice.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add llamatui/setup_voice.py tests/test_setup_voice.py
git commit -m "feat: add setup_voice.fetch_whisper (download/extract/flatten)"
```

---

### Task 3: `app.py` — resolve whisper dir from anywhere + fix the hint

**Files:**
- Modify: `llamatui/app.py` (add import + `resolve_whisper_dir`, `WhisperServer` construction, hint text)
- Test: `tests/test_app_resolve.py`

**Interfaces:**
- Consumes: `paths.default_whisper_dir()` (Task 1); `WhisperServer(whisper_dir=…, bin_path=…, model_path=…, url=…)` (existing).
- Produces: `app.resolve_whisper_dir(cwd_whisper: Path | None = None) -> Path`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_app_resolve.py`:

```python
"""resolve_whisper_dir: dev fallback to ./whisper when it holds the server binary, else the
user-data dir. Pure (cwd injected), so no App is constructed."""

from llamatui.app import resolve_whisper_dir
from llamatui.paths import default_whisper_dir


def test_prefers_local_dir_when_binary_present(tmp_path):
    (tmp_path / "whisper-server.exe").write_bytes(b"x")
    assert resolve_whisper_dir(tmp_path) == tmp_path


def test_falls_back_to_user_data_dir_when_no_local_binary(tmp_path):
    assert resolve_whisper_dir(tmp_path) == default_whisper_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest tests/test_app_resolve.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_whisper_dir'`

- [ ] **Step 3: Add the import + helper to `llamatui/app.py`**

Add to the imports near the other `from .` lines:

```python
from pathlib import Path

from .paths import default_whisper_dir
```

Add this module-level function (place it next to the other module-level helpers such as `_date_line`):

```python
def resolve_whisper_dir(cwd_whisper: Path | None = None) -> Path:
    """Dev fallback: ./whisper if it holds the server binary, else the per-user data dir."""
    local = cwd_whisper if cwd_whisper is not None else Path("whisper")
    if (local / "whisper-server.exe").exists():
        return local
    return default_whisper_dir()
```

- [ ] **Step 4: Pass the resolved dir to `WhisperServer`**

In `on_mount`, change the `WhisperServer(...)` construction to add `whisper_dir`:

```python
            self.whisper = WhisperServer(
                whisper_dir=resolve_whisper_dir(),
                bin_path=self.config.whisper_bin,
                model_path=self.config.whisper_model,
                url=self.config.whisper_url,
            )
```

- [ ] **Step 5: Fix the voice-off hint**

In `action_dictate`, change the hint text:

```python
        if self.dictation is None:
            self._voice_note("voice off — run: llamatui --setup-voice")
            return
```

- [ ] **Step 6: Run tests + import check**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest tests/test_app_resolve.py -q`
Expected: PASS (2 tests)
Run: `C:\llama\.venv\Scripts\python.exe -c "import llamatui.app; print('import ok')"`
Expected: `import ok`

- [ ] **Step 7: Commit**

```bash
git add llamatui/app.py tests/test_app_resolve.py
git commit -m "feat: resolve whisper dir from anywhere; fix voice-off hint"
```

---

### Task 4: `--setup-voice` flag in `__main__.py`

**Files:**
- Modify: `llamatui/__main__.py`
- Test: `tests/test_main_setup_voice.py`

**Interfaces:**
- Consumes: `setup_voice.fetch_whisper` (Task 2), `paths.default_whisper_dir()` (Task 1).
- Produces: the `--setup-voice` CLI flag; `main()` returns after fetching when it is set (TUI not launched).

- [ ] **Step 1: Write the failing test**

Create `tests/test_main_setup_voice.py`:

```python
"""--setup-voice fetches the assets and returns WITHOUT launching the TUI."""

import sys

import llamatui.__main__ as entry
import llamatui.app as appmod
from llamatui import setup_voice


def test_setup_voice_fetches_and_does_not_launch_tui(monkeypatch):
    called = {}

    def fake_fetch(dest, **kw):
        called["dest"] = dest
        return dest / "whisper-server.exe"

    def boom(self):
        raise AssertionError("TUI launched on --setup-voice")

    monkeypatch.setattr(setup_voice, "fetch_whisper", fake_fetch)
    monkeypatch.setattr(appmod.LlamaTUI, "run", boom)
    monkeypatch.setattr(sys, "argv", ["llamatui", "--setup-voice"])

    entry.main()
    assert "dest" in called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest tests/test_main_setup_voice.py -q`
Expected: FAIL — argparse rejects `--setup-voice` (`SystemExit`) or the TUI-launch `boom` fires.

- [ ] **Step 3: Add the flag + early-return dispatch**

In `llamatui/__main__.py`, add the argument after the `--whisper-url` line:

```python
    ap.add_argument("--setup-voice", action="store_true",
                    help="download whisper-server + model into the user-data dir, then exit")
```

And immediately after `args = ap.parse_args()`, before the `base_url` lines:

```python
    if args.setup_voice:
        from . import paths, setup_voice
        dest = paths.default_whisper_dir()
        print(f"Fetching whisper-server + model into {dest} ...")
        exe = setup_voice.fetch_whisper(dest)
        print(f"Done. whisper-server at {exe}")
        return
```

- [ ] **Step 4: Run test + flag check**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest tests/test_main_setup_voice.py -q`
Expected: PASS
Run: `C:\llama\.venv\Scripts\python.exe -m llamatui --help`
Expected: help text lists `--setup-voice`.

- [ ] **Step 5: Commit**

```bash
git add llamatui/__main__.py tests/test_main_setup_voice.py
git commit -m "feat: add llamatui --setup-voice flag"
```

---

### Task 5: `install.ps1` bootstrap + retire `get-whisper.ps1` + docs

**Files:**
- Create: `scripts/install.ps1`
- Remove: `scripts/get-whisper.ps1`
- Modify: `README.md`, `CONTEXT.md`

**Interfaces:** none (tooling + docs).

- [ ] **Step 1: Create `scripts/install.ps1`**

```powershell
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
        uv run python -m llamatui --setup-voice   # PATH-independent: uses the project env
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
```

- [ ] **Step 2: Remove the old fetch script**

```bash
git rm scripts/get-whisper.ps1
```

- [ ] **Step 3: Verify `install.ps1` parses**

Run: `pwsh -NoProfile -Command "$null=[System.Management.Automation.Language.Parser]::ParseFile('C:\llama\scripts\install.ps1',[ref]$null,[ref]$null); if($?){'parse ok'}"`
Expected: `parse ok`

- [ ] **Step 4: Rewrite the README onboarding**

In `README.md`, replace the existing `## Voice dictation (optional)` section with:

```markdown
## Install (run `llamatui` from anywhere)

Requires [uv](https://docs.astral.sh/uv/). From the repo root:

```powershell
.\scripts\install.ps1            # installs the `llamatui` command + voice/semantic extras,
                                 # then fetches whisper-server + the model (~500 MB)
.\scripts\install.ps1 -SkipVoice # ...or skip the whisper download
```

Then, from any directory:

```powershell
llamatui                         # start the TUI (needs a running llama-server)
```

Update later with `uv tool upgrade llamatui`. The conversations DB and the whisper
assets both live under `%LOCALAPPDATA%\llamatui\`, so they're found no matter where
you launch from.

## Voice dictation (optional)

Press **Ctrl+R** in the prompt to start recording, again to stop; the transcribed text
lands in the input for review and is **never auto-sent**. Transcription runs locally via
whisper.cpp `whisper-server` (CUDA), in its own folder under the user-data dir.

- Voice is set up by `install.ps1` above. To (re)fetch the binary + model on demand:
  `llamatui --setup-voice`.
- Capture uses your **default** input device. Set the right default mic in Windows sound
  settings if dictation is silent.
- Flags: `--no-voice` (disable), `--whisper-bin PATH`, `--whisper-model PATH`,
  `--whisper-url URL` (use an already-running whisper-server instead of spawning one).
```

- [ ] **Step 5: Add a `paths` line to `CONTEXT.md`**

In `CONTEXT.md`, under the domain nouns, add:

```markdown
- **paths** — `paths.py` is the single source of truth for per-user on-disk locations
  (`user_data_dir()`, `default_whisper_dir()`). The conversations **Store** DB and the
  whisper assets fetched by `llamatui --setup-voice` share this one root, so the app finds
  them regardless of the current working directory.
```

- [ ] **Step 6: Full suite (docs/script task touches no Python, must stay green)**

Run: `C:\llama\.venv\Scripts\python.exe -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/install.ps1 README.md CONTEXT.md
git commit -m "feat: install.ps1 bootstrap; retire get-whisper.ps1; onboarding docs"
```

---

## Self-Review

**Spec coverage:**
- `uv tool install ".[voice,semantic]"` → Task 5 (`install.ps1`).
- Whisper assets in `default_whisper_dir()` (user-data) → Tasks 1 (path) + 2 (fetch) + 4 (dispatch); concrete-target test in Task 2.
- Dev `./whisper` fallback + explicit-flag override → Task 3 (`resolve_whisper_dir`; `--whisper-bin`/`--whisper-model` still passed through).
- `--setup-voice` subcommand → Task 4. Hint fix → Task 3.
- `install.ps1` (+ `-SkipVoice`) → Task 5; `get-whisper.ps1` removed → Task 5.
- `storage` shares the root via `paths.user_data_dir()` (appauthor=False, DB unmoved) → Task 1.
- README + CONTEXT → Task 5.

**Placeholder scan:** every code/step has concrete content; the only intentionally-fixed external values (release version/asset/model URLs) are real and were verified live.

**Type consistency:** `paths.user_data_dir`/`default_whisper_dir`, `setup_voice.fetch_whisper(dest, *, download)` + `SERVER_EXE`/`MODEL_NAME`/`MODEL_URL`/`WHISPER_RELEASE_URL`, `app.resolve_whisper_dir(cwd_whisper)`, and the `WhisperServer(whisper_dir=…)` keyword are used identically across Tasks 1–5. `WhisperServer` already accepts `whisper_dir` (no change needed there).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-onboarding-install.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.
2. **Inline Execution** — execute here with checkpoints.

Which approach?
