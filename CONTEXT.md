# CONTEXT — llamatui domain glossary

The shared vocabulary for this codebase. These are the **seams** worth designing around;
use these names in code, comments, and reviews rather than inventing new ones.

## Domain nouns

- **Turn** — one assistant reply to one user message. A turn arrives as a *stream* and is
  folded into structured state by the **`TurnStream`** module (`turn.py`). Its `TurnState`
  separates **Thinking** from **Answer** and tracks any **Tool calls**, ttft, and usage /
  timings. `TurnStream` is also the single place that knows llama-server's non-standard wire
  shape (the content-type vocabulary and where llama.cpp hides its `timings` block) — the
  *wire-adapter* seam lives here, not smeared across the worker and metrics.

- **Thinking vs. Answer** — a turn has two text streams: the model's reasoning
  (`text_reasoning`, shown in a collapsible pane) and its actual answer (`text`). Thinking is
  *never* replayed back into context — only the answer and tool calls/results are. This
  invariant is enforced in two cooperating places: the **Conversation** (what enters history)
  and the chat client (what is sent on the wire, `client._prepare_message_for_openai`).

- **Tool call** — a model-initiated invocation of a tool (Exa web search via MCP, or the
  in-process memory tools). Represented by `turn.ToolCall` (name, streamed args, done flag,
  parsed `query`, and the captured `result`/`failed`). The UI surfaces the actual result on the
  chip so "done" can't mask a no-op or error. A small local model sometimes *leaks* a tool call
  into its answer as plain text (`<tool_call><function=…>`); `strip_tool_noise` removes that from
  the shown and persisted answer, since it never executed.

- **Conversation** — the single source of truth for an ongoing chat: the in-memory list of
  agent-facing **Messages** *and* its SQLite persistence, kept coherent behind one interface
  (`conversation.py`). Owns the lazy "create on first answered turn" rule, the
  "history holds user + answer only" rule, and cancel/undo. The **Store** (`storage.py`) is
  the raw SQLite layer it wraps.

- **Metrics** — throughput and token accounting for a turn (`metrics.py`). `extract()` folds
  usage + llama.cpp timings + wall-clock into one `TurnMetrics`; `format_oneline()` is its
  interface.

- **KnowledgeGraph** — the deep module (`graph.py`) that owns *everything about facts the
  assistant knows*: the *entities* (people/projects/preferences/…), *observations* (timestamped
  facts), and *relations* (typed edges) tables, plus the FTS5 keyword index, the embedding
  vector cache, salience scoring, hydration, and **hybrid retrieval**. Forgiving validation
  (case-insensitive names, free-text types). Callers talk to it by *intent* — `observe`,
  `search`, `salient`, `recent`, `forget`, `attach_embedder` — **never in SQL**; that narrow
  interface is the test surface (`tests/test_graph.py`). It shares one SQLite connection (from
  `storage.connect()`) with the **Store**, but owns its own tables; `Store` is now conversations
  only.
  - **Recall is hybrid**: FTS5/**BM25** keyword search fused with **semantic** (embedding)
    search via **Reciprocal Rank Fusion**, with a cosine floor so off-topic queries return
    nothing. The embedder is an *injectable seam* — the `Embedder` protocol + `build_embedder()`
    feature-detect the optional `fastembed` package and return `None` when absent (recall
    degrades to keyword-only). `attach_embedder()` is the **one public seam** for the whole
    embedding lifecycle (set + backfill); nothing reaches into graph internals.

- **Memory** — the *surface* (`memory.py`), a **thin wrapper** over the KnowledgeGraph. It owns
  only how the graph is presented to the model:
  - **Tools** the model calls itself — `remember` / `recall` / `forget` as Agent Framework
    `FunctionTool`s. Relations are *folded into* `remember` (`related_to` + `relation`) so a
    local model has fewer tools to juggle. This is the second tool *shape* in the app: contrast
    **remote MCP** (Exa web search, over HTTP, `tools.py`) with **in-process function tools**
    (memory, over the graph).
  - **`preamble()`** — an ambient context block spliced into the system prompt *just above the
    date line* (see "Cache-prefix discipline"). It is **curated, not a dump**: a **Background**
    section (salient entities, `user` pinned first) and a **Recently learned** section (newest
    observations, excluding ones already in Background), under hard size budgets.
    - **Injection defense.** Memory content is partly shaped by tools/web, so the block is
      *untrusted data*. The preamble leads with a notice (reference data, **not** instructions;
      never obey it; flag anomalies) and wraps the facts in `<saved_memory>…</saved_memory>`
      delimiters; `MEMORY_GUIDANCE` adds the write-path rule (don't store secrets, persona
      changes, or web/tool claims as the user's wishes). This is a *soft* mitigation — hard
      enforcement is structural: the tools only ever store/retrieve, never execute.

- **Instructions** — `instructions.build_instructions()` composes the system prompt so the
  cache-prefix invariant is **structural**, not a convention: `volatile` (the date line) is a
  distinct, always-last slot; stable parts (persona → capability guidance → ambient memory
  block) precede it by construction. Tested as a property in `tests/test_instructions.py`.

- **Dictation** — the *record → transcribe* state machine (`dictation.py`). `Ctrl+R` starts
  recording, again to stop and transcribe; the text lands in the prompt input for review and is
  **never auto-sent**. States: `idle → recording → transcribing → idle`, with **at most one
  recording and one transcription live at a time** — re-entrant `Ctrl+R` during `transcribing` is a
  no-op. The mic recorder and the transcriber are *injectable seams* (mirroring the `Embedder`
  protocol), so tests use a fake recorder + fake transcriber — no real audio or network. Dictation
  is **independent of the `gen` worker group**: you can dictate the next prompt while a reply still
  streams, so it owns its own **voice** segment in the `StatusBar` rather than fighting `gen` for
  the shared state line.

- **WhisperServer** — owns *a reachable local-STT endpoint* (`whisper.py`): a whisper.cpp
  `whisper-server`. This is the whisper **wire-adapter** seam — the single place that knows the
  server's request shape (16 kHz mono WAV → `/inference`) and that **normalizes its output
  vocabulary** (trim; drop non-speech annotations like `[BLANK_AUDIO]`/`(silence)`); nothing else
  touches the subprocess or the HTTP wire, exactly as `TurnStream` is the only place that knows
  llama-server's shape. **Discover-then-spawn, own only what you spawned**: `ensure_running()`
  reuses an already-running server at the configured address if one answers, else lazy-spawns one
  (isolated in the `whisper/` subdir with its own CUDA DLLs so they never collide with the repo-root
  llama stack), and `close()` terminates **only** a subprocess this instance spawned — never a
  shared server it merely connected to. `available()` (binary + model present) is pure
  feature-detection with no spawn; dictation degrades **off** when it is false.

- **paths** — `paths.py` is the single source of truth for per-user on-disk locations
  (`user_data_dir()`, `default_whisper_dir()`, `settings_path()`). The conversations
  **Store** DB, the whisper assets fetched by `llamatui --setup-voice`, and the persisted
  **Settings** file all share this one root, so the app finds them regardless of the current
  working directory.

- **Settings** — the *global, persisted user preferences* (`settings.py`): the same for
  every conversation and surviving restart — the sampling knobs (`thinking_budget`,
  `temperature`, `top_p`, `max_tokens`), the **voice mode**, and `show_thinking`. This is one
  of **three buckets** for state, distinguished by a single test:
  - **Config** — *bootstrap*: set once at launch, immutable for the session (url, model,
    db_path, whisper paths, feature enables). *"Can it change after launch?"* No → here.
  - **Settings** — *"is it the same for every conversation and persisted?"* Yes → here.
  - **Conversation** — *per-conversation state* persisted with one chat (the system prompt,
    history). *"Does it belong to one chat?"* Yes → here.

  Precedence on load is **CLI flag > saved file > built-in default** (`DEFAULTS` is the one
  source of the defaults). Loading never writes the file, so a one-off CLI flag wins for that
  run without persisting; only the Settings panel writes.

- **voice mode** — how a `Ctrl+R` key stream is mapped to dictation verbs (a **Settings**
  field). **Toggle** (default): press starts, press again stops. **Hold**: hold to record,
  release to stop — but terminals (and Textual) expose no key-release, so "release" is
  inferred from a gap in the key's OS **auto-repeat** burst. See [[Dictation]].

## Architecture stance

The Textual `App` (`app.py`) is a **thin adapter**: it wires widgets, keybindings, and the
streaming worker, but delegates the genuinely complex jobs to deep modules — `TurnStream`
(interpret the stream), `Conversation` (own history + persistence), `KnowledgeGraph` (facts +
retrieval), and `Memory` (the model-facing surface). The interface of each deep module is its
test surface; see `tests/`.

**Cache-prefix discipline.** `_rebuild_agent` builds the system prompt via
`build_instructions(persona, capabilities, ambient, volatile=date)`, which guarantees the
volatile date line lands **last** — because llama-server caches the longest stable prefix and
the date is the only daily-volatile part. The invariant lives in the builder's *shape*, not a
comment. The memory preamble is *semi*-volatile, so it is recomputed **only at conversation
boundaries** (mount, `/system`, new chat, open conversation) — never mid-turn. Within a
conversation the whole prompt is constant and its KV prefix is reused; a fact the model writes
mid-turn shows up in Background/Recent at the next conversation switch (and is findable via
`recall` in the meantime).

The agent build is split for this: `_build_instructions` composes the (semi-volatile) system
prompt and caches it + the conversation-stable tools at conversation boundaries only;
`_apply_agent` rebuilds the agent from those caches plus the current **Settings** sampling. A
mid-conversation sampling change calls `_apply_agent` alone, so the prompt — and its KV prefix —
never changes. This is also why opening the settings panel mid-stream is safe.
