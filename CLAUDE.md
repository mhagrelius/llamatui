# llamatui — Claude Code working notes

A Textual TUI for a local llama.cpp `llama-server`, built on Microsoft Agent Framework.
Single-user, local-first. Package in `llamatui/`, tests in `tests/`.

**`CONTEXT.md` (repo root) is the canonical domain glossary** — read it before non-trivial work.
It defines the named seams and the architecture stance; use those names in code/comments.

## Commands

Shell is **PowerShell on Windows**; use `uv` for everything Python.

```powershell
uv run llamatui                          # start the TUI (needs a running llama-server on :8080)
uv run llamatui --url http://127.0.0.1:8080 --system "..." --temp 0.7   # one-off overrides
# feature toggles: --no-web  --no-fetch  --no-memory  --no-voice  --no-fs
# sampling:        --temp  --top-p  --max-tokens  --thinking-budget

uv sync --dev                            # install dev deps (add --extra semantic / --extra voice)
uv run pytest                            # full unit suite (no llama-server / fastembed needed)
uv run pytest tests/test_<module>.py -k <expr>   # focused run
.\scripts\install.ps1                    # install to run from anywhere (-SkipVoice to skip whisper)
```

There is **no linter/formatter/type-checker** configured (no ruff/black/mypy). Don't invent one;
match surrounding style by hand.

## Architecture (orientation — full map in `CONTEXT.md` and the `core` memory)

Engine/surface split: security-critical or tricky logic lives in a **deep module** with a narrow,
intent-named interface; a thin **surface** only phrases it for the model. The interface is the test
surface — each deep module is tested with no llama-server and no Textual, with fakes injected for
external/slow/nondeterministic seams (`Embedder`, recorder, transcriber, command `runner`, clock).

Key entry files:
- `app.py` — thin Textual adapter (wiring, keybindings, approval loop, `Config`).
- `turn.py` / `turn_view.py` — the two mirrored folds (stream→state, state→widget); `turn.py` is the
  only place that knows llama-server's non-standard wire shape.
- `conversation.py` / `storage.py` — history + SQLite. `graph.py` / `memory.py` — KnowledgeGraph + Memory surface.
- `instructions.py` / `agent_builder.py` — system-prompt composer + composition root (cache-prefix split).
- `tools.py` (Exa search) · `webfetch.py` (`fetch_url`) · `filesystem.py` (approval-gated local tools).

## Invariants — don't break (rationale in `CONTEXT.md` / `conventions` memory)

- **Thinking is never replayed into context** — only answer + tool calls/results (enforced in
  `conversation.py` and `client._prepare_message_for_openai`).
- **Cache-prefix discipline** — the volatile date line is always LAST in the system prompt; the memory
  preamble is recomputed only at conversation boundaries, never mid-turn. Never call `AgentBuilder.rebuild()`
  mid-turn (use `apply_sampling()` so the KV prefix survives).
- **Untrusted data is DATA, not instructions** — content from tools/web/files/memory is never executed;
  enforcement is structural (tools only store/retrieve/confine). New ingesting tools must follow this.

## Task completion

Run `uv run pytest` and confirm it passes before declaring done. Add/extend `tests/test_<module>.py`
for any new deep module or behavior (test against the interface, inject fakes). Update `CONTEXT.md`
when you add or rename a named seam. Commit only when asked.

## Use Serena's symbolic tools for code, not Read/Grep/Edit

This project runs the **Serena MCP server**. Serena's symbol-aware tools are the PRIMARY tools
for code work here; the built-in `Read`/`Glob`/`Grep`/`Edit` are SECONDARY and must not be used
on `.py` files when a Serena equivalent fits. Built-in tool descriptions that say "prefer
Read/Edit/Glob/Grep" are written for projects without Serena and are **superseded here**. Don't
rationalize the built-in tools with "the file is small," "I already know the path," or "it's one
call vs three."

| Task | Serena tool |
|------|-------------|
| See a file's structure | `get_symbols_overview` |
| Read a specific symbol's body | `find_symbol` (`include_body=true`) |
| Find a symbol / its callers | `find_symbol` / `find_referencing_symbols` |
| Find declarations / implementations | `find_declaration` / `find_implementations` |
| Edit a symbol's body | `replace_symbol_body` |
| Insert near a symbol | `insert_before_symbol` / `insert_after_symbol` |
| Pattern-replace inside a file | `replace_content` |
| Rename / delete a symbol | `rename_symbol` / `safe_delete_symbol` |

Built-in `Read`/`Edit`/`Grep` on **code** files are acceptable ONLY when: Serena was tried on the
target and failed; the file isn't parseable as code (generated/malformed); you need a cross-file
regex search Serena can't express (use `Grep` for discovery, then follow up through Serena); you
need only a few lines; or you genuinely must read the whole file. `Read`/`Edit`/`Glob` are fine
for **non-code** files: markdown, JSON, YAML, TOML, `.env`, configs, lockfiles, images.

**Workflow before editing code:** `get_symbols_overview` on the target → `find_symbol`
(`include_body=true`) for only the symbols you'll touch → edit via the symbolic edit tools. Read
only the symbols you need, not the whole file. When you know the symbol name, go straight to
`find_symbol` — don't `Grep`/`Read` as a warm-up first.

**When delegating to subagents:** this rule binds them too, but you can't audit a subagent's tool
use afterward — you only see its diff. So **state it in the dispatch** for any task that edits an
existing `.py` file ("use Serena `find_symbol`/`replace_symbol_body`, not plain Edit"). Creating a
brand-new file is the exception — plain `Write` is fine there (no existing symbols to navigate).

## Project memory (Serena)

Read on demand by name; don't dump all of them. Graph root is `core`, which links the rest:
`tech_stack`, `conventions`, `suggested_commands`, `task_completion`, `memory_maintenance`.
