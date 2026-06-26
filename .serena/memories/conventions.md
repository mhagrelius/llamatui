# Conventions

## Module design (load-bearing)
- **Engine/surface split**: security-critical / tricky logic lives in a deep module with a narrow, intent-named interface; a thin "surface" only phrases it for the model. Examples: `graph.py` (KnowledgeGraph) vs `memory.py` (Memory surface); `filesystem.py` Workspace internals vs `build_tools()`/`FILESYSTEM_GUIDANCE`.
- **The interface is the test surface.** Each deep module is tested with no llama-server and no Textual. Callers talk by intent, never reach into internals (e.g. KnowledgeGraph callers never write SQL).
- **Injectable seams** for anything external/slow/nondeterministic: `Embedder` protocol + `build_embedder()`, mic recorder, transcriber, command `runner`, clock. Tests inject fakes. Mirror this for any new I/O.
- **Feature-detect optional deps** (`build_embedder()` returns None when `fastembed` absent); never hard-import an extra at module load. Feature degrades off.

## Code style
- `from __future__ import annotations` at top of every module. Type hints throughout, incl. `typing.Annotated[str, "desc"]` on tool params (the description the model sees).
- Module-level docstring explaining the seam/role, using the `CONTEXT.md` vocabulary. Dense inline comments for invariants, not narration.
- Agent-Framework tools created as `FunctionTool(func=..., name=..., description=...)`; `approval_mode="always_require"` for mutations/commands, `"never_require"` (default) for reads/auto tools.

## Prompt assembly (cache-prefix discipline) — see `agent_builder.py`, `instructions.py`
- `build_instructions(persona, capabilities, ambient, volatile)` guarantees the volatile date line is LAST (llama-server caches the longest stable prefix).
- `AgentBuilder.rebuild()` recomputes the semi-volatile prompt + tools at CONVERSATION BOUNDARIES only; `apply_sampling()` rebuilds the agent from the cached prompt mid-turn so the KV prefix survives. Never call `rebuild()` mid-turn.
- Each feature owns its when-to-use guidance string in the module that owns the tool (`tools.WEB_SEARCH_GUIDANCE`, `webfetch.FETCH_GUIDANCE`, `memory.MEMORY_GUIDANCE`, `filesystem.FILESYSTEM_GUIDANCE`); `_capabilities()` is the one seam that assembles them.

## Injection defense (untrusted data)
- Content from tools/web/files/memory is DATA, never instructions. Guidance blocks say so explicitly; hard enforcement is structural (tools only store/retrieve/confine, never execute). New tools that ingest external content must follow this and add a guidance note. When a tool wraps external content in a labeled boundary (e.g. `fetch_url`'s `fetched_url` envelope), neutralize the content so it can't forge/escape that boundary marker — structural, not just guidance.

## State buckets (where does a setting go?) — see `settings.py`
- **Config**: bootstrap, immutable for the session (url, model, db_path, feature enables) — set in `__main__.py`, passed to `app.Config`.
- **Settings**: same for every conversation + persisted (sampling, voice_mode, show_thinking). Precedence: CLI flag > saved file > built-in `DEFAULTS`. Loading never writes; only the settings panel writes.
- **Conversation**: per-chat persisted state (system prompt, history, workspace root).

## Adding a feature/tool (checklist)
1. New deep module (or extend an existing surface) following engine/surface split.
2. Add a `Config` flag + CLI arg (`--no-<x>`) in `__main__.py` and `app.Config`.
3. Wire enable in `app.on_mount`; add a branch in `AgentBuilder._capabilities()` with the feature's guidance note.
4. Reflect state in the `on_mount` status line.
5. Unit-test the deep module against its interface with injected fakes; add `tests/test_<module>.py`.
