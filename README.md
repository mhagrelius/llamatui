# llamatui

A **fun experiment in running a completely local AI assistant** — no cloud model, no API
bill, no data leaving your machine. It's a terminal UI for a local
[llama-server](https://github.com/ggml-org/llama.cpp), built on the
[Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/) and
[Textual](https://textual.textualize.io/).

The idea: take an open-weights model running on your own GPU and wrap it in something that
actually *feels* like a personal assistant — streamed answers with a distinct **thinking**
pane, live throughput **metrics** (tokens/sec, prompt vs. generation, context usage), an
optional **web search** tool the model can reach for on its own, and a **sidebar of past
conversations** persisted locally in SQLite. A small `opencode` / `pi` / `elia`-style
experience, but pointed entirely at hardware you own.

> **Scope:** this repo is just the Python app. You bring your own llama-server (the
> binaries, GPU libraries, and model weights are intentionally *not* included here).

## What's interesting about it

- **Totally local by default.** The only thing that ever leaves your machine is a web search
  query — and only if you enable the tool and the model decides to use it.
- **It remembers you.** A persistent, local **knowledge graph** (entities, facts, and how they
  relate) that the assistant builds about you across conversations — surfaced both as ambient
  context and via tools it calls itself. No server, no cloud; it lives in the same SQLite file
  as your chats.
- **Thinking vs. answer, separated.** llama.cpp streams a model's reasoning in a non-standard
  `reasoning_content` field; a small client subclass surfaces it so the TUI can show thinking
  distinctly from the final answer (and never replays it back into context).
- **Real metrics.** It reads llama.cpp's native `timings` block for true prefill/generation
  throughput and speculative-decode acceptance, not just wall-clock guesses.
- **Deep, testable modules.** The streaming state machine (`TurnStream`), the
  conversation/persistence layer (`Conversation`), and the memory knowledge-graph
  (`KnowledgeGraph`, with a thin `Memory` surface over it) are isolated behind narrow
  interfaces, so the tricky logic — including hybrid keyword+semantic recall — is unit-tested
  with no server and no UI. See [`CONTEXT.md`](CONTEXT.md).

## Prerequisites

1. A running **llama-server** (from [llama.cpp](https://github.com/ggml-org/llama.cpp))
   exposing its OpenAI-compatible endpoint, with a model loaded. Reasoning/thinking output
   and `--metrics` are nice to have.
2. [uv](https://docs.astral.sh/uv/) for running the Python app.

## Run

```sh
uv run llamatui
```

Point it somewhere else, or change defaults:

```sh
uv run llamatui --url http://127.0.0.1:8080 --system "You are concise." --temp 0.7
```

The sampling flags (`--temp`, `--top-p`, `--max-tokens`, `--thinking-budget`) default from the saved settings file; CLI flags override for that run only.

### Web search (Exa)

The model can call [Exa](https://exa.ai)'s hosted web-search MCP server on its own when a
question needs current information. It works keyless (rate-limited); set an API key to lift
the limits:

```sh
export EXA_API_KEY=your_key   # PowerShell: $env:EXA_API_KEY = "your_key"
```

Disable it entirely with `--no-web`.

### Memory

The assistant keeps a small, local **knowledge graph** about you — *entities* (people,
projects, preferences…), *facts* about them, and *relationships* between them — so it can carry
context across conversations instead of starting cold every time. It shows up two ways:

- **Ambient context.** A curated summary is injected into its system prompt each conversation —
  a **Background** section (the enduring, salient things) and a **Recently learned** section
  (what changed lately). It sits just above the date line so the rest of the prompt stays cache-
  friendly.
- **Tools it calls itself.** `remember` (save a durable fact, optionally linking two things),
  `recall` (look something up), and `forget` (drop something).

`recall` is **hybrid search**: keyword (SQLite FTS5/BM25) plus optional **semantic** search,
fused with Reciprocal Rank Fusion so paraphrases match too. Semantic search is an optional
extra — it runs a small embedding model **in-process** (no server):

```sh
uv sync --extra semantic    # pulls fastembed; first recall downloads a small model once (already included if you installed via scripts/install.ps1)
```

Without it, recall is keyword-only. Everything is stored right in your conversations database;
nothing leaves the machine. Disable memory entirely with `--no-memory`.

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
- Flags: `--no-voice` (disable), `--voice-mode {toggle,hold}` (voice input mode; toggle is default), `--whisper-bin PATH`, `--whisper-model PATH`,
  `--whisper-url URL` (use an already-running whisper-server instead of spawning one).

### Where conversations are stored

Conversations persist to a SQLite file under your user data dir
(`%LOCALAPPDATA%\llamatui\conversations.db` on Windows). Override with `--db <path>`.

## Keys

| Key           | Action                              |
| ------------- | ----------------------------------- |
| `Enter`       | send message                        |
| `Ctrl+J`      | newline in the prompt               |
| `Ctrl+N`      | new conversation                    |
| `Ctrl+B`      | toggle the conversation sidebar     |
| `Ctrl+D`      | delete the highlighted conversation |
| `Esc`         | cancel the in-flight generation     |
| `Ctrl+,`      | open the settings panel             |
| `Ctrl+C` / `Ctrl+Q` | quit                          |

Click a conversation in the sidebar (or highlight it and press `Enter`) to reopen it.

### Settings panel

Runtime preferences — thinking budget, temperature, top-p, max tokens, voice input mode (toggle vs hold-to-talk), and thinking-pane visibility — live in the settings panel (`Ctrl+,`) and persist to `%LOCALAPPDATA%\llamatui\settings.json`. CLI flags override the saved values for one run. Note: the thinking-budget setting is honored only when llama-server was started without `--reasoning-budget`.

## Slash commands

Type these in the prompt:

- `/new` — start a new conversation
- `/system <text>` — set/replace the system prompt
- `/help` — list commands
- `/exit`, `/quit` — leave

## Development

```sh
uv sync --dev
uv run pytest
```

The unit tests exercise `TurnStream` (feed recorded stream updates, assert the parsed turn),
`Conversation` (round-trip through a temp SQLite file), `KnowledgeGraph` (writing, hybrid recall
with a fake embedder, scoring, forget), `Memory` (tool wording + the ambient preamble), and the
instructions builder — no llama-server, and no `fastembed`, required.

## Why

Mostly for fun, and to see how close a fully local setup can get to the hosted-assistant
experience on consumer hardware. Turns out: pretty close.
