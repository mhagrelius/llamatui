# Voice dictation for llamatui — design

**Status:** approved (design), pending implementation plan
**Date:** 2026-06-24

## Goal

Press a shortcut, speak, and have the transcribed text dropped into the prompt
input field for review and editing — never auto-sent. Transcription runs fully
locally, in keeping with the app's local-first/private ethos.

## Decisions (locked)

| Question | Decision |
|---|---|
| Where STT runs | Local **whisper.cpp `whisper-server`**, reusing the local ggml/CUDA stack |
| Recording UX | **Toggle**: press to start, press again to stop, then transcribe |
| Server lifecycle | **TUI launches it on demand** (lazy spawn, killed on exit) |
| Default model | **`small.en`** (~460 MB); configurable to base/medium |
| Obtaining binary+model | **Part of the deliverable** — a setup script downloads them |
| Shortcut | **`Ctrl+R`** |

## Non-goals (YAGNI)

- No auto-send after transcription. Text lands in the input; the user presses Enter.
- No true push-to-talk (hold-to-talk) — terminals don't give reliable key-release.
- No cloud STT, no streaming/partial transcription, no voice output (TTS).
- No live waveform/VU meter — a status-bar indicator is enough.
- No multi-language UI; default model is English (`*.en`). Model path is
  configurable, so a multilingual model can be dropped in, but no extra UI for it.

## Architecture

The codebase rule (`CONTEXT.md`): one module = one concern, narrow interface,
`app.py` stays a thin adapter. This feature adds **two deep modules** plus a thin
binding in `app.py`. Each module's interface is its test surface.

### Module: `whisper.py` → `WhisperServer`

Owns *the whisper-server process and the transcription endpoint*. Nothing else
touches the subprocess or the HTTP wire shape.

Interface:

- `available() -> bool` — binary **and** model both present (used for feature
  detection / banner / graceful degrade). Pure, no spawn.
- `ensure_running() -> None` — lazy, idempotent: spawn the subprocess on a free
  localhost port and block until health-check passes. No-op if already running.
  Raises a typed error on spawn/health failure.
- `transcribe(wav_bytes: bytes) -> str` — POST the WAV to the endpoint, return the
  text. Calls `ensure_running()` first. Raises a typed error on HTTP failure.
- `close() -> None` — terminate the subprocess if running; idempotent.

Internals:
- **Discovery**: resolve binary (default `whisper/whisper-server.exe`, then PATH)
  and model (default `whisper/ggml-small.en.bin`); overridable via config.
- **Spawn**: pick a free localhost port; launch with `cwd` set to the binary's
  directory (the `whisper/` subdir) so its bundled DLLs resolve there, not against
  the repo-root llama DLLs. Pass `--model`, `--host 127.0.0.1`, `--port`.
- **Health**: poll the server until ready or a timeout elapses.
- **Transcribe**: multipart POST of a 16 kHz mono WAV to the server's
  transcription endpoint (`/inference`), `response_format=text` (or parse JSON);
  return the trimmed transcript.

### Module: `dictation.py` → `Dictation`

Owns *the record → transcribe state machine*. Knows nothing about the subprocess
or HTTP — it depends on a `WhisperServer` (and a capture seam) through their
interfaces.

States: `idle → recording → transcribing → idle`.

Interface:
- `toggle()` — idle→recording opens the mic stream; recording→idle closes it and
  kicks off transcription.
- `state` — current state (for the status indicator).
- Emits the finished transcript text to the app (callback or Textual message).

Internals:
- **Capture**: `sounddevice` `InputStream`, 16 kHz, 1 channel, int16, callback
  filling a buffer on sounddevice's own thread (does not block the event loop).
- On stop: assemble buffer → WAV bytes in memory (`wave` + `io.BytesIO`).
- **Injectable seams** (mirrors the `Embedder` protocol): the recorder and the
  transcriber are injected, so tests use a fake recorder (canned bytes) and fake
  transcriber (canned text) — no real audio or network in tests.

### `app.py` (thin adapter — additions only)

- Construct `WhisperServer` + `Dictation` in `on_mount` when `config.voice` and
  `WhisperServer.available()`; otherwise leave dictation off.
- New `action_dictate()` → `Dictation.toggle()`.
- On transcript ready: `PromptArea.insert(text)` at the cursor; refocus prompt.
- Status bar reflects state: `🎙 recording`, `transcribing…`, back to `ready`.
- `on_unmount`: `WhisperServer.close()` (alongside the existing web-tool close).
- Startup banner gains a `voice on/off` segment, like `web search`/`memory`.

### `PromptArea` (`widgets.py`) — additions only

- Handle `ctrl+r` in `_on_key` (same pattern as `enter`→`Submitted` and `ctrl+j`):
  `prevent_default`, `stop`, and `post_message(self.Dictate())`. Firing from the
  focused widget guarantees the key works while typing.
- New `Dictate` message class alongside `Submitted`.

## Data flow (one dictation)

```
Ctrl+R in PromptArea → Dictate message → app.action_dictate()
  → Dictation.toggle()
     idle → recording:   open sounddevice InputStream (own thread → buffer)
                         status: "🎙 recording"
     recording → idle:   close stream → WAV bytes
                         @work(thread=True):
                            WhisperServer.ensure_running()   # spawn+health, first time only
                            text = WhisperServer.transcribe(wav)
                         call_from_thread → PromptArea.insert(text) at cursor
                         status: "ready"
```

All blocking work (spawn, health-check, HTTP) runs in a Textual
`@work(thread=True)` worker — same off-event-loop pattern as `_load_embedder`.
Mic capture is callback-driven on sounddevice's thread. The event loop never
stalls.

Dictation is **independent of the `gen` worker group**: the user can dictate the
next prompt while a reply is still streaming, and `Esc` (cancel generation) does
not affect a recording.

## Process lifecycle & the DLL-conflict gotcha

- whisper-server is spawned **once, lazily** on first record; reused thereafter;
  killed in `on_unmount`.
- Port is an **auto-picked free localhost port**, avoiding any clash with
  llama-server.
- **DLL isolation**: prebuilt whisper.cpp ships its own ggml DLLs that may differ
  from llama.cpp's in the repo root. whisper-server.exe, its DLLs, and the model
  all live in a dedicated **`whisper/` subdir**; the process is spawned with `cwd`
  set there so its DLLs resolve locally. The repo-root llama stack is untouched.

## Config & graceful degradation

New `Config` fields and `__main__` args (mirroring `--no-web` / `--no-memory`):

| Arg | Config field | Default |
|---|---|---|
| `--no-voice` | `voice: bool` | `True` (enabled) |
| `--whisper-bin PATH` | `whisper_bin: str \| None` | discover `whisper/whisper-server.exe`, then PATH |
| `--whisper-model PATH` | `whisper_model: str \| None` | `whisper/ggml-small.en.bin` |

Graceful degradation (same shape as web/memory feature detection):
- `sounddevice` not installed, or no input device → dictation **off**.
- Binary or model missing (`available()` is false) → dictation **off**, banner
  says so, and `Ctrl+R` shows a one-line hint pointing at the setup script.
- `sounddevice` is a new **optional extra `[voice]`** in `pyproject.toml` (like
  `[semantic]` for fastembed), keeping the base install lean.

## Obtaining whisper-server + the model (deliverable)

`scripts/get-whisper.ps1`:
1. Download a prebuilt whisper.cpp **Windows** release (whisper-server.exe + its
   bundled DLLs) into `whisper/`.
2. Download `ggml-small.en.bin` from Hugging Face into `whisper/`.

Plus a README section documenting setup, the `[voice]` extra, and the CLI flags.

## Error handling

| Failure | Behavior |
|---|---|
| `sounddevice` missing / no mic | dictation off (banner note); no crash |
| binary/model missing | off + hint pointing at `scripts/get-whisper.ps1` |
| spawn / health timeout | status `whisper failed to start`; stays off this session |
| HTTP / transcription error | status `transcription failed`; typed text untouched |
| empty transcript | quiet no-op |

## Testing

Interface = test surface, per the codebase. No real audio or network in tests.

- **`WhisperServer`** (`tests/test_whisper.py`):
  - discovery: finds binary+model; `available()` false when either is absent.
  - request shaping & response parsing against a fake HTTP response.
  - subprocess is **not** actually spawned — test the pure helpers (discovery,
    port pick, request build, response parse) with the spawn seam stubbed.
- **`Dictation`** (`tests/test_dictation.py`):
  - state transitions idle→recording→transcribing→idle via `toggle()`.
  - injected fake recorder (canned WAV bytes) + fake transcriber (canned text);
    assert the emitted transcript and that empty audio is a no-op.

## Files touched / added

- **add** `llamatui/whisper.py` — `WhisperServer`
- **add** `llamatui/dictation.py` — `Dictation`
- **add** `scripts/get-whisper.ps1` — fetch binary + model
- **add** `tests/test_whisper.py`, `tests/test_dictation.py`
- **edit** `llamatui/app.py` — wire modules, `action_dictate`, status, banner, unmount
- **edit** `llamatui/widgets.py` — `PromptArea` `Dictate` message + `ctrl+r`
- **edit** `llamatui/__main__.py` — `--no-voice`, `--whisper-bin`, `--whisper-model`
- **edit** `pyproject.toml` — `[voice]` extra (`sounddevice`)
- **edit** `README.md` — setup + usage
- **edit** `CONTEXT.md` — add `WhisperServer` and `Dictation` to the glossary
