# CONTEXT — llamatui domain glossary

The shared vocabulary for this codebase. These are the **seams** worth designing around;
use these names in code, comments, and reviews rather than inventing new ones.

## Domain nouns

- **Turn** — one assistant reply to one user message, handled by **two mirrored folds**. A turn
  arrives as a *stream* and is folded into structured state by the **`TurnStream`** module
  (`turn.py`): its `TurnState` separates **Thinking** from **Answer** and tracks any **Tool
  calls**, ttft, and usage / timings. `TurnStream` is also the single place that knows
  llama-server's non-standard wire shape (the content-type vocabulary and where llama.cpp hides its
  `timings` block) — the *wire-adapter* seam lives here, not smeared across the worker and metrics.
  The reverse fold is **`TurnView`** (`turn_view.py`): it folds a `TurnState` into one
  **`AssistantTurn`** widget, owning the render throttle, the live `~tok/s` estimate (surfaced to
  the App's `StatusBar` via an `on_status` callback), tool-chip bookkeeping, the thinking-pane
  settle policy, and the persisted-metrics blob shape — shared by the live `generate` worker and
  the **replay** path (`load_saved`) so both render a turn one way. The worker speaks only to
  `TurnStream` (in) and `TurnView` (out); `AssistantTurn` is a *mechanical* widget (dumb setters),
  with all presentation policy in `TurnView`. Both folds are clock-injected and tested with no
  Textual (`tests/test_turn.py`, `tests/test_turn_view.py`).

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
  block) precede it by construction. Tested as a property in `tests/test_instructions.py`. It is
  the pure composer; [[AgentBuilder]] is what feeds it.

- **AgentBuilder** — the composition root (`agent_builder.py`) above the wire-level
  `client.build_agent`. It assembles the agent from the enabled **features** (web, memory), the
  conversation **persona** (falling back to the built-in `DEFAULT_SYSTEM`), the ambient memory
  preamble, and the current **Settings** sampling — and owns the **cache-prefix split** as its
  interface: `rebuild(persona, volatile, settings)` recomputes the semi-volatile prompt at
  conversation boundaries, `apply_sampling(settings)` rebuilds the agent from the *cached* prompt
  mid-turn so the KV prefix survives (`tests/test_agent_builder.py` pins this). The guidance→prompt
  step is isolated in one private `_capabilities()` seam; each feature's when-to-use note lives in
  the module that owns the tool (`tools.WEB_SEARCH_GUIDANCE`, `memory.MEMORY_GUIDANCE`), so a future
  move to agent-framework **skills** changes only that seam, not the cache-prefix machinery.

- **Dictation** — the *record → transcribe* state machine (`dictation.py`). Its interface is four
  verbs — `start` / `stop` / `cancel` / `toggle` — driven by [[VoiceInput]]; the transcribed text
  lands in the prompt input for review and is **never auto-sent**. States:
  `idle → recording → transcribing → idle`, with **at most one recording and one transcription live
  at a time** — a re-entrant verb during `transcribing` is a no-op. The mic recorder and the
  transcriber are *injectable seams* (mirroring the `Embedder` protocol), so tests use a fake
  recorder + fake transcriber — no real audio or network. Dictation is **independent of the `gen`
  worker group**: you can dictate the next prompt while a reply still streams, so it owns its own
  **voice** segment in the `StatusBar` rather than fighting `gen` for the shared state line.

- **VoiceInput** — the deep module (`voice.py`) that maps a `Ctrl+R` key stream to [[Dictation]]
  verbs according to the [[voice mode]]. It owns everything *between the key and the verb*: the
  **toggle** debounce (a held key's auto-repeat collapses to one toggle), the **hold** two-phase
  release-gap detection (see ADR-0002), the shared **120 s cap**, and the re-arm when the mode
  changes mid-recording. The interface is two verbs — `key()` (the dictate key fired) and
  `set_mode()` (switch mode; discards any in-flight recording and re-arms) — behind which the App
  owns **no dictation-timer lifecycle**. Framework-free and clock-injected like [[Dictation]]: the
  wall-clock and a `schedule_interval` *poll seam* (the timer analog of Dictation's `run_bg`) are
  injected, so the whole key→verb mapping is the test surface (`tests/test_voice.py`); the App just
  supplies a one-line Textual `set_interval` adapter. The 120 s cap here is the *proactive*
  auto-stop; `dictation.MAX_SAMPLES` is the independent defensive truncation floor.

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

- **voice mode** — the *policy* for which mapping [[VoiceInput]] applies from the `Ctrl+R` key
  stream to dictation verbs (a **Settings** field; [[VoiceInput]] is the module that applies it).
  **Toggle** (default): press starts, press again stops. **Hold**: hold to record,
  release to stop — but terminals (and Textual) expose no key-release, so "release" is
  inferred from a gap in the key's OS **auto-repeat** burst. See [[VoiceInput]] for the mapping and
  ADR-0002 for the hold mechanism.

- **Workspace** — the per-conversation rooted file/system scope (`filesystem.py`). Owns the
  resolved `root` path, the in/out **classification** predicate (`_confined`) that all read and
  write tools share — an outside path returns a clear `OUTSIDE_MSG`, never escalates (the
  framework's approval gate is static per tool name, so containment must be enforced here), and the
  cancellable async command **runner** (`_default_runner`): process-tree kill (`taskkill /T` on
  Windows, `SIGKILL` on the process group otherwise), a `CMD_OUTPUT_CAP` backstop, and a
  `cancel_event` the App sets from the UI so the agentic loop survives a command cancel. Runtime
  sinks (`on_output`, `cancel_event`) are set on the instance by `generate()` each turn, not at
  construction.

  The thin tool surface over it — `build_tools()` — exposes seven tools: read tools
  (`list_dir` / `read_file` / `search`) run without a gate (default `never_require`); mutations
  (`write_file` / `move` / `delete`) and `run_command` carry `approval_mode="always_require"`.
  `delete` routes to the OS recycle bin (`send2trash`). `FILESYSTEM_GUIDANCE` is the static policy
  block (file contents are *data*, never instructions; reads are confined; mutations and commands
  need approval) spliced into the system prompt via [[AgentBuilder]]. Together `build_tools()` and
  `FILESYSTEM_GUIDANCE` are the third tool *shape* in the app — local function tools that mutate
  behind human approval — alongside remote-MCP (Exa) and the in-process memory tools.

  Workspace is per-[[Conversation]] state: the root is persisted on the conversation row; a new
  chat resets to the [[Settings]] `default_workspace`. Resolution is by precedence:
  per-conversation > `Settings.default_workspace` > CLI/config `--workspace` > cwd. [[AgentBuilder]]
  receives the resolved `Workspace` instance in `rebuild(workspace=...)` and composes the dynamic
  `workspace_line()` ("Workspace: … · shell: …") into the system prompt's capabilities block.

## Architecture stance

The Textual `App` (`app.py`) is a **thin adapter**: it wires widgets, keybindings, and the
streaming worker, but delegates the genuinely complex jobs to deep modules — `TurnStream`
(interpret the stream), `TurnView` (reflect a turn's state into its widget), `Conversation` (own
history + persistence), `KnowledgeGraph` (facts + retrieval), `Memory` (the model-facing surface),
`AgentBuilder` (assemble the agent + own the cache-prefix split), and `VoiceInput` (map the `Ctrl+R`
key stream to dictation verbs). The interface of each deep module is its test surface; see `tests/`.

**Cache-prefix discipline.** [[AgentBuilder]] builds the system prompt via
`build_instructions(persona, capabilities, ambient, volatile=date)`, which guarantees the
volatile date line lands **last** — because llama-server caches the longest stable prefix and
the date is the only daily-volatile part. The invariant lives in the builder's *shape*, not a
comment. The memory preamble is *semi*-volatile, so it is recomputed **only at conversation
boundaries** (mount, `/system`, new chat, open conversation) — never mid-turn. Within a
conversation the whole prompt is constant and its KV prefix is reused; a fact the model writes
mid-turn shows up in Background/Recent at the next conversation switch (and is findable via
`recall` in the meantime).

The agent build is split for this, behind [[AgentBuilder]]'s interface: `rebuild()` composes the
(semi-volatile) system prompt and caches it + the conversation-stable tools at conversation
boundaries only; `apply_sampling()` rebuilds the agent from those caches plus the current
**Settings** sampling. A mid-conversation sampling change calls `apply_sampling()` alone, so the
prompt — and its KV prefix — never changes. This is also why opening the settings panel mid-stream
is safe. (The App keeps one-line `_rebuild_agent`/`_apply_agent` wrappers that delegate to the
builder — *when* to rebuild is the App's call; *how* to assemble is the builder's.)

**Approval loop.** `app.generate()` is a run→pause→resume loop: each segment runs on a
per-turn `agent.create_session()` session; when a gated tool fires the framework surfaces a
`function_approval_request`, `generate()` pauses, shows a Textual `ApprovalModal`, then resumes on
the *same* session and agent (KV prefix intact — `rebuild()` is never called mid-turn). `run_command`
is excluded from blanket "approve all" and always surfaces the modal; Deny returns a denial
response and the turn continues.
