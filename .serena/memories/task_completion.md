# Task Completion

When a coding task is considered done:

1. **Run the tests**: `uv run pytest` (full suite must pass; no llama-server / fastembed needed). For focused work, `uv run pytest tests/test_<module>.py` first, then the full suite before declaring done.
2. **Add/extend tests** for any new deep module or behavior — the module's interface is the test surface (`tests/test_<module>.py`). New seams get fakes injected, not real I/O.
3. **No linter/formatter/type-checker is configured** (no ruff/black/mypy in `pyproject.toml`). Do NOT invent one. Match surrounding style by hand: `from __future__ import annotations`, type hints, dense module docstring in `CONTEXT.md` vocabulary.
4. **Update `CONTEXT.md`** if you added or changed a named seam/domain noun — it is the canonical glossary and must stay accurate.
5. **Keep invariants intact** (see `mem:core`): thinking never replayed; cache-prefix (volatile date last, no mid-turn `rebuild()`); untrusted-data framing for any external content.
6. Commit only when the user asks. End commit messages per the repo's existing convention.

Verify before claiming done — run the command and confirm output; don't assert success without evidence.
