# Tech Stack

- **Language**: Python `>=3.11`. Package manager / runner: **uv** (`uv.lock` committed). Build backend: hatchling; wheel packages = `["llamatui"]`.
- **Core deps** (`pyproject.toml`): `agent-framework-core` + `agent-framework-openai` (>=1.8.2, Microsoft Agent Framework), `httpx>=0.27`, `mcp>=1.9`, `platformdirs>=4`, `send2trash>=1.8`, `textual>=0.86`, `trafilatura>=2.1` (HTML→main-content extraction for `fetch_url`).
- **Optional extras** (degrade gracefully when absent; feature-detected, never hard-imported at module load):
  - `semantic` → `fastembed>=0.3` (in-process embeddings for hybrid recall; keyword-only without it).
  - `voice` → `sounddevice>=0.4` (mic capture; dictation off without it).
- **Dev group**: `textual-dev>=1.7`, `pytest>=8`, `pytest-asyncio>=0.23`. `asyncio_mode = "auto"`; `testpaths = ["tests"]`.
- **External runtime (not in repo)**: a running llama.cpp `llama-server` (OpenAI-compatible endpoint, default `http://127.0.0.1:8080`). Voice also uses a whisper.cpp `whisper-server` fetched into `whisper/` under the user-data dir.
- **Platform**: primary dev/target is **Windows** (PowerShell). Code is cross-platform where it matters (e.g. process-tree kill, shell selection in `filesystem.py`).

Optional-dependency pattern: add new optional features as `[project.optional-dependencies]` extras and feature-detect at runtime, mirroring `semantic`/`voice`.
