# Voice dictation for llamatui — design

**Status:** approved (design), pending implementation plan
**Date:** 2026-06-24 (revised after grilling — see `docs/adr/0001-whisper-as-discovered-cuda-service.md`)

## Goal

Press a shortcut, speak, and have the transcribed text dropped into the prompt
input field for review and editing — never auto-sent. Transcription runs fully
locally, in keeping with the app's local-first/private ethos.

## Decisions (locked)

| Question | Decision |
|---|---|
| Where STT runs | Local **whisper.cpp `whisper-server`**, its **own CUDA 12.8 build** (Blackwell-aligned), isolated in `whisper/` — *not* sharing the repo-root llama DLLs |
| Recording UX | **Toggle**: press to start, press again to stop, then transcribe |
| Server lifecycle | **Discover-then-spawn, own only what you spawned**: reuse an external whisper-server if one answers, else lazy-spawn one (killed on exit) |
| Re-entrancy | While `transcribing`, `Ctrl+R` is a **no-op** ("still transcribing…") — at most one recording + one transcription live at a time |
| Spawn latency | **Warm at recording start**: poke `ensure_running()` in the background while the user speaks, so spawn+load hides under their speech |
| Default model | **`small.en`** (~460 MB); configurable to base/medium |
| Obtaining binary+model | **Part of the deliverable** — a setup script downloads the CUDA release + DLLs + model |
| Shortcut | **`Ctrl+R`** |
| Recording bound | **120 s** hard cap → auto-stop and transcribe |

## Non-goals (YAGNI)

- No auto-send after transcription. Text lands in the input; the user presses Enter.
- No true push-to-talk (hold-to-talk) — terminals don't give reliable key-release.
- No cloud STT, no streaming/partial transcription, no voice output (TTS).
- No live waveform/VU meter — a status-bar indicator is enough.
- No multi-language UI; default model is English (`*.en`). Model path is
  configurable, so a multilingual model can be dropped in, but no extra UI for it.
- No explicit "discard recording" path in v1 (the min-duration guard, bracket
  filtering, and never-auto-send make a mistaken recording cheap to delete).
- No in-process resampling and no device-picker UI in v1 — capture at 16 kHz on
  the default input device; fail honestly if the device won't open at 16 kHz.

## Architecture

The codebase rule (`CONTEXT.md`): one module = one concern, narrow interface,
`app.py` stays a thin adapter. This feature adds **two deep modules** plus a thin
binding in `app.py`. Each module's interface is its test surface.

### Module: `whisper.py` → `WhisperServer`

Owns *a reachable transcription endpoint* — discovering an external whisper-server
or spawning+owning a local one. It is the whisper **wire-adapter** seam: the single
place that knows the server's request shape *and* normalizes its output vocabulary.
Nothing else touches the subprocess or the HTTP wire.

Interface:

- `available() -> bool` — binary **and** model both present (used for feature
  detection / banner / graceful degrade). **Pure, no spawn, no stream open.**
- `ensure_running() -> None` — **discover-then-spawn**, lazy and idempotent:
  health-check the configured address first; if a server answers, use it and spawn
  nothing. Otherwise spawn a subprocess on a free localhost port and block until the
  health-check passes. No-op if we already have a running endpoint. Raises a typed
  error on spawn/health failure.
- `transcribe(wav_bytes: bytes) -> str` — POST the WAV to the endpoint, return the
  **normalized** text (trimmed; non-speech annotations like `[BLANK_AUDIO]` /
  `(silence)` collapsed to empty). Calls `ensure_running()` first. Raises a typed
  error on HTTP failure.
- `close() -> None` — terminate the subprocess **only if this instance spawned it**;
  never kill a server we merely connected to. Idempotent.

Internals:
- **Discovery**: resolve binary (default `whisper/whisper-server.exe`, then PATH)
  and model (default `whisper/ggml-small.en.bin`); overridable via config. Resolve
  the endpoint address from `--whisper-url` (default `127.0.0.1`, auto-picked port).
- **Reuse**: if the configured address already answers a health-check, adopt it and
  set no "spawned" flag. Trust whatever answers — no model/version validation (single
  user controls the port, mirroring how `client.py` trusts the llama-server URL).
- **Spawn**: pick a free localhost port; launch with `cwd` set to the binary's
  directory (the `whisper/` subdir) so its bundled **CUDA DLLs** resolve there, not
  against the repo-root llama DLLs. Pass `--model`, `--host 127.0.0.1`, `--port`.
  Record that *we* spawned it (guards `close()`).
- **Health**: poll the server until ready or a timeout elapses.
- **Transcribe**: multipart POST of a 16 kHz mono WAV to `/inference`,
  `response_format=text` (or parse JSON); trim, then drop the transcript if it is
  wholly a bracket/paren-wrapped non-speech annotation (`^[\[(].*[\])]$`).

### Module: `dictation.py` → `Dictation`

Owns *the record → transcribe state machine*. Knows nothing about the subprocess,
HTTP, or the cursor — it depends on a `WhisperServer` (and a capture seam) through
their interfaces.

States: `idle → recording → transcribing → idle`. **At most one recording and one
transcription live at a time.**

Interface:
- `toggle()` —
  - `idle → recording`: open the mic stream **and** fire `WhisperServer.ensure_running()`
    in the background (warm-at-start), so spawn+load overlaps with the user speaking.
  - `recording → idle`: close the stream, drop the recording if shorter than the
    min-duration guard (~300–500 ms), else assemble the WAV and kick off transcription.
  - `transcribing → *`: **no-op** (status flashes "still transcribing…").
- `state` — current state (for the status indicator).
- Emits the finished transcript text to the app (callback or Textual message).

Internals:
- **Capture**: `sounddevice` `InputStream`, **16 kHz, 1 channel, int16**, callback
  filling a buffer on sounddevice's own thread (does not block the event loop).
  Open the stream at 16 kHz directly (WASAPI shared mode resamples); if it won't
  open at 16 kHz, dictation goes **off for the session** with a clear status — no
  in-process resampling fallback in v1.
- **Min-duration guard**: a recording shorter than ~300–500 ms of captured audio is
  a quiet no-op (drops sub-blip mistaps and whisper's near-silence hallucinations).
- **120 s cap**: auto-stop and transcribe on reaching the cap; status notes
  `recording stopped (max length)`.
- On stop: assemble buffer → WAV bytes in memory (`wave` + `io.BytesIO`).
- **Injectable seams** (mirrors the `Embedder` protocol): the recorder and the
  transcriber are injected, so tests use a fake recorder (canned bytes) and fake
  transcriber (canned text) — no real audio or network in tests.

### `app.py` (thin adapter — additions only)

- Construct `WhisperServer` + `Dictation` in `on_mount` when `config.voice` and
  `WhisperServer.available()`; otherwise leave dictation off.
- New `action_dictate()` → `Dictation.toggle()`. If dictation is off (unavailable),
  show a one-line hint pointing at the setup script instead.
- On transcript ready: `PromptArea.insert_transcript(text)` (spacing rule below);
  refocus prompt. Runs via `call_from_thread` from the transcribe worker.
- **Voice status segment**: dictation writes its own `voice` segment in the
  `StatusBar` (`🎙 recording` / `transcribing…` / cleared), so it does **not** fight
  the `gen` worker for the shared `state`/`detail` line during concurrent
  record-while-streaming.
- `on_unmount`: `WhisperServer.close()` (alongside the existing web-tool close) —
  which is a no-op when we adopted an external server.
- Startup banner gains a `voice on/off` segment, like `web search`/`memory`.

### `PromptArea` (`widgets.py`) — additions only

- Handle `ctrl+r` in `_on_key` (same pattern as `enter`→`Submitted` and `ctrl+j`):
  `prevent_default`, `stop`, and `post_message(self.Dictate())`. Firing from the
  focused widget guarantees the key works while typing.
- New `Dictate` message class alongside `Submitted`.
- New `insert_transcript(text)` — insert at the cursor; **prepend one space** iff the
  char before the cursor is non-whitespace and not an opening bracket/quote; no
  trailing space; leave whisper's capitalization/punctuation untouched; **cursor
  lands after** the inserted text. Deep modules stay cursor-ignorant; this is the
  one place spacing lives.

### `StatusBar` (`widgets.py`) — additions only

- `show()` gains a `voice` field; `StatusBar` **remembers** the voice indicator and
  re-renders it on every `show()`, so a `gen`-driven `_status()` repaint never erases
  it. `_status` callers are unchanged; dictation gets a small `_voice_status()` helper.

## Data flow (one dictation)

```
Ctrl+R in PromptArea → Dictate message → app.action_dictate()
  → Dictation.toggle()
     idle → recording:   open sounddevice InputStream (own thread → buffer)
                         fire WhisperServer.ensure_running() in background  # warm-at-start
                         voice status: "🎙 recording"
     recording → idle:   close stream
                         if too short → quiet no-op
                         else WAV bytes →
                            @work(thread=True):
                               WhisperServer.ensure_running()   # already warm; near-instant
                               text = WhisperServer.transcribe(wav)   # trimmed + de-annotated
                            call_from_thread → PromptArea.insert_transcript(text)
                         voice status: "transcribing…" → cleared
     transcribing → *:   no-op ("still transcribing…")
```

All blocking work (spawn, health-check, HTTP) runs in a Textual
`@work(thread=True)` worker — same off-event-loop pattern as `_load_embedder`.
Mic capture is callback-driven on sounddevice's thread. The event loop never stalls.

Dictation is **independent of the `gen` worker group**: the user can dictate the
next prompt while a reply is still streaming (the prompt `TextArea` stays editable;
only *Enter* is gated by `_busy`), and `Esc` (cancel generation) does not affect a
recording. The voice status segment keeps the two from colliding on the status line.

## Process lifecycle & the DLL-conflict gotcha

- whisper-server is **discovered if already running**, else spawned **once, lazily**
  (warmed at first record-start); reused thereafter; the spawned one is killed in
  `on_unmount`. A server we merely connected to is **never** killed.
- Port is an **auto-picked free localhost port** for a spawned server, avoiding any
  clash with llama-server.
- **DLL isolation**: the prebuilt whisper.cpp CUDA release ships its own ggml/cuBLAS
  DLLs that differ from llama.cpp's in the repo root. whisper-server.exe, its DLLs,
  and the model all live in a dedicated **`whisper/` subdir**; the process is spawned
  with `cwd` set there so its DLLs resolve locally. The repo-root llama stack is
  untouched. (See ADR-0001.)

## Config & graceful degradation

New `Config` fields and `__main__` args (mirroring `--no-web` / `--no-memory`):

| Arg | Config field | Default |
|---|---|---|
| `--no-voice` | `voice: bool` | `True` (enabled) |
| `--whisper-bin PATH` | `whisper_bin: str \| None` | discover `whisper/whisper-server.exe`, then PATH |
| `--whisper-model PATH` | `whisper_model: str \| None` | `whisper/ggml-small.en.bin` |
| `--whisper-url URL` | `whisper_url: str \| None` | `127.0.0.1` + auto-picked port (spawn local) |

Graceful degradation (same shape as web/memory feature detection):
- `sounddevice` not installed, or no input device → dictation **off**.
- Binary or model missing (`available()` is false) → dictation **off**, banner
  says so, and `Ctrl+R` shows a one-line hint pointing at the setup script.
- Mic won't open at 16 kHz → dictation **off for the session** with a clear status.
- `sounddevice` is a new **optional extra `[voice]`** in `pyproject.toml` (like
  `[semantic]` for fastembed), keeping the base install lean.

## Obtaining whisper-server + the model (deliverable)

`scripts/get-whisper.ps1`:
1. Download a prebuilt whisper.cpp **Windows CUDA** release (whisper-server.exe +
   its bundled CUDA/cuBLAS DLLs), version-matched to the driver, into `whisper/`.
2. Download `ggml-small.en.bin` from Hugging Face into `whisper/`.

Plus a README section documenting setup, the `[voice]` extra, and the CLI flags.

## Error handling

| Failure | Behavior |
|---|---|
| `sounddevice` missing / no mic | dictation off (banner note); no crash |
| binary/model missing | off + hint pointing at `scripts/get-whisper.ps1` |
| mic won't open at 16 kHz | status `mic unavailable`; off this session |
| spawn / health timeout (no external server either) | status `whisper failed to start`; stays off this session (surfaced mid-recording via warm-at-start) |
| HTTP / transcription error | status `transcription failed`; typed text untouched |
| empty / non-speech-only transcript | quiet no-op (bracket-stripped in `WhisperServer`) |
| recording hits 120 s cap | auto-stop + transcribe; status `recording stopped (max length)` |

## Testing

Interface = test surface, per the codebase. No real audio or network in tests.

- **`WhisperServer`** (`tests/test_whisper.py`):
  - discovery: finds binary+model; `available()` false when either is absent.
  - **discover-then-spawn**: when the configured address answers, adopt it and do
    not spawn; `close()` does not kill an adopted server.
  - request shaping & response parsing against a fake HTTP response, including
    **non-speech normalization** (`[BLANK_AUDIO]` / `(silence)` → empty).
  - subprocess is **not** actually spawned — test the pure helpers (discovery,
    port pick, request build, response parse, de-annotation) with the spawn seam stubbed.
- **`Dictation`** (`tests/test_dictation.py`):
  - state transitions idle→recording→transcribing→idle via `toggle()`.
  - **re-entrant `toggle()` during `transcribing` is a no-op**.
  - **warm-at-start**: entering `recording` calls `ensure_running()` once.
  - **min-duration guard** and **120 s cap** behaviors.
  - injected fake recorder (canned WAV bytes) + fake transcriber (canned text);
    assert the emitted transcript and that empty/too-short audio is a no-op.

## Files touched / added

- **add** `llamatui/whisper.py` — `WhisperServer`
- **add** `llamatui/dictation.py` — `Dictation`
- **add** `scripts/get-whisper.ps1` — fetch CUDA binary + DLLs + model
- **add** `tests/test_whisper.py`, `tests/test_dictation.py`
- **add** `docs/adr/0001-whisper-as-discovered-cuda-service.md` (done)
- **edit** `llamatui/app.py` — wire modules, `action_dictate`, voice status, banner, unmount
- **edit** `llamatui/widgets.py` — `PromptArea` `Dictate` + `insert_transcript`; `StatusBar` voice segment
- **edit** `llamatui/__main__.py` — `--no-voice`, `--whisper-bin`, `--whisper-model`, `--whisper-url`
- **edit** `pyproject.toml` — `[voice]` extra (`sounddevice`)
- **edit** `README.md` — setup + usage
- **edit** `CONTEXT.md` — `WhisperServer` and `Dictation` glossary entries (done)
