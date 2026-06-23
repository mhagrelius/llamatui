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

- **Tool call** — a model-initiated invocation of a remote tool (today: Exa web search via
  MCP). Represented by `turn.ToolCall` (name, streamed args, done flag, parsed `query`).

- **Conversation** — the single source of truth for an ongoing chat: the in-memory list of
  agent-facing **Messages** *and* its SQLite persistence, kept coherent behind one interface
  (`conversation.py`). Owns the lazy "create on first answered turn" rule, the
  "history holds user + answer only" rule, and cancel/undo. The **Store** (`storage.py`) is
  the raw SQLite layer it wraps.

- **Metrics** — throughput and token accounting for a turn (`metrics.py`). `extract()` folds
  usage + llama.cpp timings + wall-clock into one `TurnMetrics`; `format_oneline()` is its
  interface.

## Architecture stance

The Textual `App` (`app.py`) is a **thin adapter**: it wires widgets, keybindings, and the
streaming worker, but delegates the two genuinely complex jobs to deep modules — `TurnStream`
(interpret the stream) and `Conversation` (own history + persistence). The interface of each
deep module is its test surface; see `tests/`.
