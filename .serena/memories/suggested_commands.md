# Suggested Commands

Shell is **PowerShell on Windows**. Use uv for everything Python.

## Run
- `uv run llamatui` — start the TUI (needs a running llama-server).
- `uv run llamatui --url http://127.0.0.1:8080 --system "..." --temp 0.7` — override defaults for one run.
- Feature toggles: `--no-web`, `--no-fetch`, `--no-memory`, `--no-voice`, `--no-fs`. Sampling: `--temp`, `--top-p`, `--max-tokens`, `--thinking-budget`. Other: `--workspace`, `--db`, `--voice-mode {toggle,hold}`, `--setup-voice`.

## Dev / test
- `uv sync --dev` — install dev deps. `uv sync --extra semantic` / `--extra voice` for optional features.
- `uv run pytest` — run the full unit suite (no llama-server, no fastembed required).
- `uv run pytest tests/test_<module>.py` — single module; `-k <expr>` to filter.

## Install (run from anywhere)
- `.\scripts\install.ps1` (add `-SkipVoice` to skip whisper download). Update: `uv tool upgrade llamatui`.

## No linter/formatter/type-checker configured
There is no ruff/black/mypy config in `pyproject.toml`. Match existing style by hand. See `mem:task_completion`.

## Windows notes
- User data (conversations DB, whisper assets, settings.json) lives under `%LOCALAPPDATA%\llamatui\`.
- PowerShell env var: `$env:EXA_API_KEY = "..."` (not `export`).
