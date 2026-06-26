# Core

`llamatui` — Textual TUI for a local llama.cpp `llama-server`, built on Microsoft Agent Framework. Single-user, local-first. Python package lives in `llamatui/`; tests in `tests/`.

## Canonical reference
**`CONTEXT.md` (repo root) is the authoritative domain glossary** — read it before non-trivial work. It defines the named seams (Turn/TurnStream/TurnView, Conversation/Store, KnowledgeGraph/Memory, Instructions, AgentBuilder, Dictation/VoiceInput, WhisperServer, Workspace, Settings/Config buckets) and the architecture stance. Use those names in code/comments/reviews; don't invent new ones.

## Source map (`llamatui/`)
- `app.py` — Textual `App` (thin adapter): wiring, keybindings, the `generate()` approval loop, `Config` dataclass. Delegates real logic to deep modules.
- `turn.py` / `turn_view.py` — the two mirrored folds (stream→state, state→widget). `turn.py` is the ONLY place that knows llama-server's non-standard wire shape.
- `conversation.py` / `storage.py` — history + SQLite persistence; `Store` is the raw SQLite layer.
- `graph.py` / `memory.py` — KnowledgeGraph engine + thin Memory surface (tools + ambient preamble).
- `instructions.py` / `agent_builder.py` — system-prompt composer + composition root owning the cache-prefix split.
- `client.py` — wire-level `build_agent`; subclass surfacing llama.cpp `reasoning_content`.
- `tools.py` — remote MCP tools, restricted to Exa `web_search_exa` (search only; retrieval is owned by `fetch_url`). `webfetch.py` — WebFetcher deep module + `fetch_url` (HTTP page→markdown, scheme check + manual redirect following; auto-run, network egress). `filesystem.py` — Workspace + approval-gated local tools.
- `metrics.py` — `TurnMetrics`: token/throughput numbers extracted from a streamed turn (MAF usage + llama-server `timings`). `setup_voice.py` — one-shot whisper-server/model fetch (`--setup-voice`).
- `dictation.py` / `voice.py` / `whisper.py` — voice dictation state machine, key→verb mapping, local STT endpoint.
- `settings.py` / `settings_screen.py` / `paths.py` — persisted prefs, settings UI, per-user on-disk locations.
- `approval.py` / `widgets.py` — approval modal + custom widgets.

## Invariants (don't break)
- **Thinking is never replayed into context** — only answer + tool calls/results. Enforced in `conversation.py` and `client._prepare_message_for_openai`.
- **Cache-prefix discipline**: volatile date line is always LAST in the system prompt; memory preamble recomputed only at conversation boundaries, never mid-turn. See `mem:conventions`.
- **Deep module = test surface**: security/tricky logic isolated behind a narrow interface, tested with no server/UI. Injectable seams (Embedder, recorder, transcriber, command runner) for tests.
- **Four tool shapes**: remote-MCP (Exa `web_search_exa`, auto); in-process function tools (memory, auto); in-process network-egress auto tool (`fetch_url`); approval-gated local tools (filesystem mutations + run_command).

See `mem:tech_stack`, `mem:conventions`, `mem:suggested_commands`, `mem:task_completion`.
