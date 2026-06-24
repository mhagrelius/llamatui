# Voice Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Press `Ctrl+R` in the prompt, speak a paragraph, and have locally-transcribed text dropped into the input for review — never auto-sent.

**Architecture:** Two deep modules behind narrow interfaces — `WhisperServer` (the whisper.cpp wire-adapter: discover-then-spawn a CUDA `whisper-server`, shape the `/inference` request, normalize the output) and `Dictation` (the `idle → recording → transcribing → idle` state machine with injectable recorder + transcriber seams). `app.py` stays a thin adapter that wires them, owns the background-work seam, and paints a dedicated `voice` status segment. See the design at [docs/superpowers/specs/2026-06-24-voice-dictation-design.md](docs/superpowers/specs/2026-06-24-voice-dictation-design.md) and the rationale at [docs/adr/0001-whisper-as-discovered-cuda-service.md](docs/adr/0001-whisper-as-discovered-cuda-service.md).

**Tech Stack:** Python 3.11+, Textual, httpx, `sounddevice` (optional `[voice]` extra), whisper.cpp `whisper-server` (CUDA 12.8), pytest.

## Global Constraints

- `requires-python = ">=3.11"`; all new modules start with `from __future__ import annotations`.
- One module = one concern; `app.py` stays a thin adapter. Each module's interface is its test surface (`tests/`).
- **No real audio or network in tests** — recorder, transcriber, HTTP client, and subprocess spawn are all injectable seams stubbed with fakes (mirror `FakeEmbedder` in `tests/test_graph.py`).
- Capture audio is **16 kHz, 1 channel, int16**. whisper-server endpoint is `/inference`; default model `whisper/ggml-small.en.bin`; default binary `whisper/whisper-server.exe`.
- **Own only what you spawned:** `WhisperServer.close()` must never terminate a server it merely connected to.
- Graceful degradation mirrors web/memory: missing dependency/binary/model/device → dictation **off**, no crash.
- Use `httpx` (already a transitive dep via the agent framework / mcp) for HTTP. Do not add new base dependencies; `sounddevice` goes in the optional `[voice]` extra only.

---

### Task 0 (pre-flight): Verify the whisper-server wire shape against the real binary

**Why first:** Task 1's `transcribe()` and `_healthy()` encode four *unverified* assumptions about the whisper.cpp `server` HTTP API. The unit tests fake the HTTP, so they pass even if the contract is wrong — only a real probe catches it. Do this once before writing Task 1, and correct Task 1's constants if any assumption is off.

**Files:** none (manual verification; pulls `scripts/get-whisper.ps1` from Task 6 forward, or fetch the binary + `ggml-small.en.bin` into `whisper/` by hand).

- [ ] **Step 1: Get the binary + model**

Run Task 6's `scripts/get-whisper.ps1` now (or download `whisper-server.exe` + DLLs + `ggml-small.en.bin` into `whisper/` manually).

- [ ] **Step 2: Start the server**

```bash
whisper/whisper-server.exe -m whisper/ggml-small.en.bin --host 127.0.0.1 --port 8088
```
Note in the startup log **when** it begins listening relative to model load (warm-at-start assumes the HTTP port opens only *after* the model is loaded).

- [ ] **Step 3: Probe the four assumptions**

```bash
# health: does GET / return 200 once loaded?
curl -s -o /dev/null -w "root=%{http_code}\n" http://127.0.0.1:8088/
# inference: is the path /inference, the field `file`, and the body the transcript?
curl -s http://127.0.0.1:8088/inference -F file=@sample.wav -F response_format=text
```
(Make a throwaway 16 kHz mono `sample.wav` first, e.g. record one in Audacity or `ffmpeg`.)

- [ ] **Step 4: Record the confirmed contract**

Confirm and, if any differ, patch Task 1 before implementing:
- endpoint path (`/inference`?)
- multipart field name (`file`?)
- `response_format=text` returns the transcript as the response **body** (`r.text`)?
- `GET /` → 200, and only after the model is loaded?

Write the confirmed values here as a one-line note so the rest of the plan is grounded:
`CONFIRMED: path=___ field=___ resp=body/json health=GET / → ___`

---

### Task 1: `WhisperServer` — the whisper wire-adapter (discover-then-spawn)

**Files:**
- Create: `llamatui/whisper.py`
- Test: `tests/test_whisper.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces:
  - `class WhisperError(RuntimeError)`
  - `class WhisperServer` with `__init__(self, bin_path: str | None = None, model_path: str | None = None, url: str | None = None, whisper_dir: str | Path = "whisper", health_timeout: float = 30.0, _spawn=subprocess.Popen, _client=httpx)`
  - `WhisperServer.available() -> bool`
  - `WhisperServer.ensure_running() -> None`
  - `WhisperServer.transcribe(wav_bytes: bytes) -> str`
  - `WhisperServer.close() -> None`
  - module-level `_clean_transcript(raw: str) -> str` (pure)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_whisper.py`:

```python
"""WhisperServer's interface is the test surface: discovery, discover-then-spawn,
request shaping, output normalization. No real subprocess, no real network — the HTTP
client and the spawn function are injected fakes (like FakeEmbedder in test_graph.py)."""

import pytest

from llamatui.whisper import WhisperServer, WhisperError, _clean_transcript


# ---- pure output normalization ---------------------------------------------------------
def test_clean_transcript_trims():
    assert _clean_transcript("  hello world  \n") == "hello world"

def test_clean_transcript_drops_non_speech_annotations():
    assert _clean_transcript("[BLANK_AUDIO]") == ""
    assert _clean_transcript("(silence)") == ""
    assert _clean_transcript("[ Pause ]") == ""

def test_clean_transcript_keeps_real_speech_with_punctuation():
    assert _clean_transcript("Ship it. (finally)") == "Ship it. (finally)"


# ---- fakes -----------------------------------------------------------------------------
class FakeResp:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

class FakeHTTP:
    """Records GET/POST calls; serves canned responses."""
    def __init__(self, get_ok=True, post_text="hello"):
        self.get_ok = get_ok
        self.post_text = post_text
        self.posts = []
        self.gets = []
    def get(self, url, timeout=None):
        self.gets.append(url)
        return FakeResp(status=200 if self.get_ok else 503)
    def post(self, url, files=None, data=None, timeout=None):
        self.posts.append({"url": url, "files": files, "data": data})
        return FakeResp(text=self.post_text)

class FakeProc:
    def __init__(self):
        self.terminated = False
    def terminate(self):
        self.terminated = True


# ---- available() -----------------------------------------------------------------------
def test_available_true_when_bin_and_model_present(tmp_path):
    (tmp_path / "whisper-server.exe").write_bytes(b"x")
    (tmp_path / "ggml-small.en.bin").write_bytes(b"x")
    ws = WhisperServer(bin_path=str(tmp_path / "whisper-server.exe"),
                       model_path=str(tmp_path / "ggml-small.en.bin"))
    assert ws.available() is True

def test_available_false_when_model_missing(tmp_path):
    (tmp_path / "whisper-server.exe").write_bytes(b"x")
    ws = WhisperServer(bin_path=str(tmp_path / "whisper-server.exe"),
                       model_path=str(tmp_path / "nope.bin"))
    assert ws.available() is False


# ---- discover-then-spawn ---------------------------------------------------------------
def test_adopts_external_server_without_spawning(tmp_path):
    http = FakeHTTP(get_ok=True)
    spawn_calls = []
    def fake_spawn(*a, **k):
        spawn_calls.append((a, k))
        return FakeProc()
    ws = WhisperServer(url="http://127.0.0.1:9999", _client=http, _spawn=fake_spawn)
    ws.ensure_running()
    assert spawn_calls == []                 # adopted, never spawned
    ws.close()                               # must NOT kill an adopted server (nothing to kill)

def test_spawns_when_no_server_answers(tmp_path):
    # first GET (configured-url probe) fails; after spawn, health GET succeeds
    class FlakyHTTP(FakeHTTP):
        def __init__(self):
            super().__init__()
            self._calls = 0
        def get(self, url, timeout=None):
            self.gets.append(url)
            self._calls += 1
            return FakeResp(status=200 if self._calls > 1 else 503)
    http = FlakyHTTP()
    proc = FakeProc()
    ws = WhisperServer(bin_path="whisper/whisper-server.exe",
                       model_path="whisper/ggml-small.en.bin",
                       _client=http, _spawn=lambda *a, **k: proc, health_timeout=2.0)
    ws.ensure_running()
    ws.close()
    assert proc.terminated is True           # we spawned it, so we kill it

def test_concurrent_ensure_running_spawns_once():
    # warm-at-start + transcribe both call ensure_running concurrently on the first dictation.
    import threading
    http = FakeHTTP(get_ok=True)             # health passes on the first poll after spawn
    spawns = []
    ws = WhisperServer(bin_path="whisper/whisper-server.exe",
                       model_path="whisper/ggml-small.en.bin",
                       _client=http, _spawn=lambda *a, **k: (spawns.append(1), FakeProc())[1],
                       health_timeout=2.0)
    threads = [threading.Thread(target=ws.ensure_running) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(spawns) == 1                   # the lock guarantees exactly one spawn


# ---- transcribe ------------------------------------------------------------------------
def test_transcribe_posts_wav_and_returns_text(tmp_path):
    http = FakeHTTP(get_ok=True, post_text="the quick brown fox")
    ws = WhisperServer(url="http://127.0.0.1:9999", _client=http, _spawn=lambda *a, **k: FakeProc())
    out = ws.transcribe(b"RIFFfake")
    assert out == "the quick brown fox"
    assert http.posts[0]["url"].endswith("/inference")
    assert "file" in http.posts[0]["files"]

def test_transcribe_normalizes_non_speech(tmp_path):
    http = FakeHTTP(get_ok=True, post_text="[BLANK_AUDIO]")
    ws = WhisperServer(url="http://127.0.0.1:9999", _client=http, _spawn=lambda *a, **k: FakeProc())
    assert ws.transcribe(b"RIFFfake") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_whisper.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llamatui.whisper'`

- [ ] **Step 3: Write the implementation**

Create `llamatui/whisper.py`:

```python
"""WhisperServer — owns a reachable local STT endpoint (whisper.cpp ``whisper-server``).

The whisper *wire-adapter* seam: the one place that knows the server's request shape
(16 kHz mono WAV → ``/inference``) and normalizes its output vocabulary. Discover-then-spawn,
own only what you spawned — see docs/adr/0001-whisper-as-discovered-cuda-service.md.
"""

from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from shutil import which

import httpx

# whisper emits bracket/paren-wrapped non-speech annotations on silence/noise, e.g.
# "[BLANK_AUDIO]", "(silence)", "[ Pause ]". A transcript that is wholly such a token is empty.
_NON_SPEECH = re.compile(r"^[\[(].*[\])]$", re.DOTALL)


class WhisperError(RuntimeError):
    """Typed failure for spawn/health/transcription problems."""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _clean_transcript(raw: str) -> str:
    text = raw.strip()
    if not text or _NON_SPEECH.match(text):
        return ""
    return text


class WhisperServer:
    def __init__(
        self,
        bin_path: str | None = None,
        model_path: str | None = None,
        url: str | None = None,
        whisper_dir: str | Path = "whisper",
        health_timeout: float = 30.0,
        _spawn=subprocess.Popen,
        _client=httpx,
    ) -> None:
        self._dir = Path(whisper_dir)
        self._bin = Path(bin_path) if bin_path else self._discover_bin()
        self._model = Path(model_path) if model_path else (self._dir / "ggml-small.en.bin")
        self._configured_url = url.rstrip("/") if url else None
        self._health_timeout = health_timeout
        self._spawn = _spawn
        self._client = _client
        self._proc = None                 # set ONLY if we spawned the server
        self._endpoint: str | None = None
        self._lock = threading.Lock()     # serializes discover/spawn (warm + transcribe race)

    # ---- discovery -------------------------------------------------------
    def _discover_bin(self) -> Path:
        local = self._dir / "whisper-server.exe"
        if local.exists():
            return local
        found = which("whisper-server")
        return Path(found) if found else local

    def available(self) -> bool:
        """Pure feature-detect: binary AND model both present. No spawn, no stream."""
        return self._bin.exists() and self._model.exists()

    # ---- lifecycle -------------------------------------------------------
    def _healthy(self, base: str) -> bool:
        try:
            r = self._client.get(base + "/", timeout=1.0)
            return r.status_code < 500
        except Exception:
            return False

    def ensure_running(self) -> None:
        """Discover-then-spawn, lazy + idempotent. Adopt an answering server; else spawn one.

        The lock serializes the discover/spawn decision: warm-at-start and the transcribe path
        both call this concurrently on the first dictation, and without it both could spawn.
        """
        if self._endpoint and self._healthy(self._endpoint):
            return
        with self._lock:
            # re-check inside the lock — the other worker may have finished while we waited
            if self._endpoint and self._healthy(self._endpoint):
                return
            if self._configured_url and self._healthy(self._configured_url):
                self._endpoint = self._configured_url      # adopted — leave self._proc None
                return
            port = _free_port()
            base = self._configured_url or f"http://127.0.0.1:{port}"
            try:
                self._proc = self._spawn(
                    [str(self._bin), "--model", str(self._model),
                     "--host", "127.0.0.1", "--port", str(port)],
                    cwd=str(self._bin.parent),
                )
            except Exception as exc:
                raise WhisperError(f"failed to spawn whisper-server: {exc}") from exc
            deadline = time.monotonic() + self._health_timeout
            while time.monotonic() < deadline:
                if self._healthy(base):
                    self._endpoint = base
                    return
                time.sleep(0.2)
            self.close()
            raise WhisperError("whisper-server did not become healthy in time")

    def transcribe(self, wav_bytes: bytes) -> str:
        self.ensure_running()
        assert self._endpoint is not None
        try:
            r = self._client.post(
                self._endpoint + "/inference",
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data={"response_format": "text"},
                timeout=120.0,
            )
            r.raise_for_status()
        except Exception as exc:
            raise WhisperError(f"transcription request failed: {exc}") from exc
        return _clean_transcript(r.text)

    def close(self) -> None:
        """Terminate ONLY a subprocess we spawned. Never kill an adopted server."""
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
            self._endpoint = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_whisper.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add llamatui/whisper.py tests/test_whisper.py
git commit -m "feat: add WhisperServer wire-adapter (discover-then-spawn)"
```

---

### Task 2: `Dictation` — the record → transcribe state machine

**Files:**
- Create: `llamatui/dictation.py`
- Test: `tests/test_dictation.py`
- Modify: `pyproject.toml` (add `[voice]` optional extra)

**Interfaces:**
- Consumes: `WhisperServer` (Task 1) satisfies the `Transcriber` protocol via `ensure_running()` + `transcribe(wav_bytes) -> str`.
- Produces:
  - `class State(str, Enum)` with members `IDLE`, `RECORDING`, `TRANSCRIBING`
  - `class Dictation` with `__init__(self, recorder, transcriber, run_bg, on_text, on_state=..., on_note=...)`, `.toggle() -> None`, `.state -> State` property
  - `def build_recorder() -> Recorder | None`
  - constants `SAMPLE_RATE = 16_000`, `MIN_SAMPLES`, `MAX_SAMPLES`
  - `run_bg` seam contract: `run_bg(work: Callable[[], object], done: Callable[[object], None]) -> None` runs `work()` off the event loop, then calls `done(result)` **on the UI thread**. So every `Dictation` callback (`on_text`/`on_state`/`on_note`) fires on the UI thread.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_dictation.py`:

```python
"""Dictation's interface is the test surface: state transitions, re-entrancy, warm-at-start,
the min-duration guard, and the 120 s truncation cap. No real audio or network — the recorder,
transcriber, and background-work seam are all fakes (like FakeEmbedder in test_graph.py)."""

import wave
import io

from llamatui.dictation import Dictation, State, SAMPLE_RATE, MIN_SAMPLES, MAX_SAMPLES


class FakeRecorder:
    def __init__(self, pcm=b""):
        self.pcm = pcm
        self.started = False
    def start(self):
        self.started = True
    def stop(self):
        self.started = False
        return self.pcm


class FakeTranscriber:
    def __init__(self, text="hello"):
        self.text = text
        self.ensure_calls = 0
        self.last_wav = None
    def ensure_running(self):
        self.ensure_calls += 1
    def transcribe(self, wav_bytes):
        self.last_wav = wav_bytes
        return self.text


def run_sync(work, done):
    done(work())


class DeferBg:
    """Holds (work, done) without running, so we can observe TRANSCRIBING state mid-flight."""
    def __init__(self):
        self.pending = []
    def __call__(self, work, done):
        self.pending.append((work, done))
    def flush(self):
        for work, done in self.pending:
            done(work())
        self.pending = []


def _pcm(n_samples):
    return b"\x00\x00" * n_samples   # int16 silence; len-based guards only care about byte count


def _make(recorder, transcriber, run_bg=run_sync):
    texts, states, notes = [], [], []
    d = Dictation(
        recorder=recorder, transcriber=transcriber, run_bg=run_bg,
        on_text=texts.append, on_state=states.append, on_note=notes.append,
    )
    return d, texts, states, notes


def test_full_cycle_idle_recording_transcribing_idle():
    rec = FakeRecorder(pcm=_pcm(SAMPLE_RATE))     # 1 s of audio, above the guard
    stt = FakeTranscriber(text="ship it")
    d, texts, states, notes = _make(rec, stt)
    assert d.state is State.IDLE
    d.toggle()                                     # idle -> recording
    assert d.state is State.RECORDING
    assert rec.started is True
    d.toggle()                                     # recording -> transcribing -> (sync) idle
    assert d.state is State.IDLE
    assert texts == ["ship it"]
    assert State.RECORDING in states and State.TRANSCRIBING in states


def test_warm_at_start_calls_ensure_running_on_record():
    rec = FakeRecorder(pcm=_pcm(SAMPLE_RATE))
    stt = FakeTranscriber()
    d, *_ = _make(rec, stt)
    d.toggle()                                     # entering recording warms the server
    assert stt.ensure_calls >= 1


def test_reentrant_toggle_while_transcribing_is_noop():
    rec = FakeRecorder(pcm=_pcm(SAMPLE_RATE))
    stt = FakeTranscriber()
    bg = DeferBg()
    d, texts, states, notes = _make(rec, stt, run_bg=bg)
    d.toggle()                                     # recording (warm deferred)
    d.toggle()                                     # -> transcribing (transcribe deferred)
    assert d.state is State.TRANSCRIBING
    d.toggle()                                     # re-entrant: no-op
    assert d.state is State.TRANSCRIBING
    assert any("transcrib" in n.lower() for n in notes)
    bg.flush()
    assert d.state is State.IDLE


def test_min_duration_guard_is_quiet_noop():
    rec = FakeRecorder(pcm=_pcm(MIN_SAMPLES - 1))   # too short
    stt = FakeTranscriber()
    d, texts, states, notes = _make(rec, stt)
    d.toggle()
    d.toggle()
    assert d.state is State.IDLE
    assert texts == []
    assert stt.last_wav is None                     # never transcribed


def test_empty_transcript_does_not_insert():
    rec = FakeRecorder(pcm=_pcm(SAMPLE_RATE))
    stt = FakeTranscriber(text="")                  # WhisperServer already normalized to ""
    d, texts, states, notes = _make(rec, stt)
    d.toggle(); d.toggle()
    assert texts == []
    assert d.state is State.IDLE


def test_120s_cap_truncates_wav():
    rec = FakeRecorder(pcm=_pcm(MAX_SAMPLES + SAMPLE_RATE))   # 1 s over the cap
    stt = FakeTranscriber()
    d, *_ = _make(rec, stt)
    d.toggle(); d.toggle()
    with wave.open(io.BytesIO(stt.last_wav), "rb") as w:
        assert w.getnframes() == MAX_SAMPLES
        assert w.getframerate() == SAMPLE_RATE
        assert w.getnchannels() == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_dictation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llamatui.dictation'`

- [ ] **Step 3: Write the implementation**

Create `llamatui/dictation.py`:

```python
"""Dictation — the record → transcribe state machine.

Knows nothing about HTTP, the subprocess, or the cursor. It depends on a recorder seam and a
transcriber seam (``WhisperServer``) through their interfaces, and on a ``run_bg`` seam that runs
blocking work off the event loop and delivers the result back on the UI thread. So tests inject
fakes and the whole machine stays synchronous and framework-free.
"""

from __future__ import annotations

import io
import wave
from enum import Enum
from typing import Callable, Protocol

SAMPLE_RATE = 16_000
MIN_SAMPLES = SAMPLE_RATE // 3          # ~333 ms guard: drops sub-blip mistaps + silence hallucinations
MAX_SAMPLES = SAMPLE_RATE * 120        # 120 s hard cap (defensive truncation; app also auto-stops)


class State(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


class Recorder(Protocol):
    def start(self) -> None: ...
    def stop(self) -> bytes:
        """Stop capture and return raw int16 mono PCM at 16 kHz."""
        ...


class Transcriber(Protocol):
    def ensure_running(self) -> None: ...
    def transcribe(self, wav_bytes: bytes) -> str: ...


RunBg = Callable[[Callable[[], object], Callable[[object], None]], None]


def _to_wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)            # int16
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class Dictation:
    def __init__(
        self,
        recorder: Recorder,
        transcriber: Transcriber,
        run_bg: RunBg,
        on_text: Callable[[str], None],
        on_state: Callable[[State], None] = lambda s: None,
        on_note: Callable[[str], None] = lambda m: None,
    ) -> None:
        self._rec = recorder
        self._stt = transcriber
        self._run_bg = run_bg
        self._on_text = on_text
        self._on_state = on_state
        self._on_note = on_note
        self._state = State.IDLE

    @property
    def state(self) -> State:
        return self._state

    def _set(self, s: State) -> None:
        self._state = s
        self._on_state(s)

    # ---- the one public verb --------------------------------------------
    def toggle(self) -> None:
        if self._state is State.IDLE:
            self._start()
        elif self._state is State.RECORDING:
            self._stop()
        else:  # TRANSCRIBING
            self._on_note("still transcribing…")

    # ---- idle -> recording ----------------------------------------------
    def _start(self) -> None:
        self._rec.start()
        self._set(State.RECORDING)
        # warm-at-start: spawn/health overlaps with the user speaking.
        self._run_bg(self._warm_work, self._warm_done)

    def _warm_work(self) -> object:
        try:
            self._stt.ensure_running()
            return None
        except Exception as exc:  # noqa: BLE001 - surfaced as a status note
            return exc

    def _warm_done(self, result: object) -> None:
        if isinstance(result, Exception):
            self._on_note(f"whisper failed to start: {result}")

    # ---- recording -> transcribing --------------------------------------
    def _stop(self) -> None:
        pcm = self._rec.stop()
        n_samples = len(pcm) // 2          # int16 = 2 bytes/sample
        if n_samples < MIN_SAMPLES:
            self._set(State.IDLE)          # quiet no-op
            return
        if n_samples > MAX_SAMPLES:
            pcm = pcm[: MAX_SAMPLES * 2]
        wav = _to_wav(pcm)
        self._set(State.TRANSCRIBING)
        self._run_bg(lambda: self._transcribe_work(wav), self._transcribe_done)

    def _transcribe_work(self, wav: bytes) -> object:
        try:
            return self._stt.transcribe(wav)
        except Exception as exc:  # noqa: BLE001 - surfaced as a status note
            return exc

    def _transcribe_done(self, result: object) -> None:
        if isinstance(result, Exception):
            self._on_note(f"transcription failed: {result}")
        elif result:
            self._on_text(str(result))
        self._set(State.IDLE)


# ---- real capture (optional [voice] extra) -------------------------------
class SoundDeviceRecorder:
    """Captures 16 kHz mono int16 via sounddevice on its own callback thread."""

    def __init__(self, samplerate: int = SAMPLE_RATE) -> None:
        import sounddevice as sd  # lazily imported; optional dependency
        self._sd = sd
        self._sr = samplerate
        self._frames: list[bytes] = []
        self._stream = None

    def start(self) -> None:
        self._frames = []

        def cb(indata, frames, time_info, status):  # runs on sounddevice's thread
            self._frames.append(bytes(indata))

        self._stream = self._sd.RawInputStream(
            samplerate=self._sr, channels=1, dtype="int16", callback=cb
        )
        self._stream.start()

    def stop(self) -> bytes:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return b"".join(self._frames)


def build_recorder() -> Recorder | None:
    """A SoundDeviceRecorder, or None if sounddevice is absent or there is no input device."""
    try:
        import sounddevice as sd
    except Exception:
        return None
    try:
        if not any(d["max_input_channels"] > 0 for d in sd.query_devices()):
            return None
    except Exception:
        return None
    return SoundDeviceRecorder()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_dictation.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Declare `httpx` in base deps + add the `[voice]` optional extra**

In `pyproject.toml`, add `httpx` to the base `dependencies` list (it is currently only a
transitive dep, but `WhisperServer` imports it at module top and is constructed unconditionally
when voice is enabled — an undeclared import here would be a startup crash, not a graceful degrade):

```toml
dependencies = [
    "agent-framework-core>=1.8.2",
    "agent-framework-openai>=1.8.2",
    "httpx>=0.27",
    "mcp>=1.9",
    "platformdirs>=4",
    "textual>=0.86",
]
```

Then, under `[project.optional-dependencies]`, add below the `semantic` line:

```toml
# Voice dictation (local STT capture via sounddevice). Optional: without it, dictation is off.
# Install with `uv sync --extra voice`. Also needs the whisper-server binary + model — see
# scripts/get-whisper.ps1.
voice = ["sounddevice>=0.4"]
```

- [ ] **Step 6: Commit**

```bash
git add llamatui/dictation.py tests/test_dictation.py pyproject.toml
git commit -m "feat: add Dictation state machine + [voice] extra"
```

---

### Task 3: `PromptArea` dictate binding + `StatusBar` voice segment

**Files:**
- Modify: `llamatui/widgets.py`
- Test: `tests/test_widgets.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `PromptArea.Dictate` message class
  - `PromptArea.insert_transcript(text: str) -> None`
  - module-level pure helper `_needs_leading_space(before: str) -> bool`
  - module-level pure helper `render_status(*, model: str, state: str, detail: str, connected: bool, voice: str) -> Text`
  - `StatusBar.show(*, model=None, state=None, detail=None, connected=None, voice=None) -> None` (now stateful — remembers each field, re-renders on every call)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_widgets.py`:

```python
"""Pure helpers behind the widgets are the test surface: leading-space logic for inserted
transcripts, and the stateful status line that must never let a gen repaint erase the voice
segment. No Textual app is spun up here."""

from llamatui.widgets import _needs_leading_space, render_status


def test_needs_leading_space_after_word():
    assert _needs_leading_space("g") is True          # "...the bug" + dictation

def test_no_leading_space_at_start_or_after_space():
    assert _needs_leading_space("") is False
    assert _needs_leading_space(" ") is False
    assert _needs_leading_space("\n") is False

def test_no_leading_space_after_opener():
    assert _needs_leading_space("(") is False
    assert _needs_leading_space('"') is False


def test_render_status_includes_all_segments():
    t = render_status(model="qwen", state="ready", detail="ctx 10k", connected=True, voice="🎙 recording")
    plain = t.plain
    assert "qwen" in plain and "ready" in plain and "ctx 10k" in plain and "🎙 recording" in plain

def test_render_status_omits_empty_voice():
    t = render_status(model="qwen", state="ready", detail="", connected=True, voice="")
    assert "🎙" not in t.plain
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_widgets.py -v`
Expected: FAIL with `ImportError: cannot import name '_needs_leading_space'`

- [ ] **Step 3: Add the `Dictate` message + `ctrl+r` handling + `insert_transcript`**

In `llamatui/widgets.py`, replace the `PromptArea` class body so it gains the `Dictate` message, the `ctrl+r` key branch, and `insert_transcript` (keep the existing `Submitted` + `enter`/`ctrl+j` behavior unchanged):

```python
_OPENERS = set("([{“”\"'`")


def _needs_leading_space(before: str) -> bool:
    """Prepend a space iff the char before the cursor is real text (not start/space/opener)."""
    if before == "" or before.isspace():
        return False
    return before not in _OPENERS


class PromptArea(TextArea):
    """A multi-line prompt. Enter submits; Ctrl+J inserts a newline; Ctrl+R dictates."""

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class Dictate(Message):
        pass

    def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "ctrl+j":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        if event.key == "ctrl+r":
            event.prevent_default()
            event.stop()
            self.post_message(self.Dictate())
            return
        super()._on_key(event)

    def insert_transcript(self, text: str) -> None:
        """Insert a dictated transcript at the cursor with a single leading space when needed.

        Leaves whisper's capitalization/punctuation untouched; the cursor lands after the
        inserted text (TextArea.insert moves it). This is the one place dictation spacing lives.
        """
        if _needs_leading_space(self._char_before_cursor()):
            text = " " + text
        self.insert(text)

    def _char_before_cursor(self) -> str:
        row, col = self.cursor_location
        if col == 0:
            return "\n" if row > 0 else ""
        line = self.document.get_line(row)
        return line[col - 1] if 0 <= col - 1 < len(line) else ""
```

- [ ] **Step 4: Make `StatusBar` stateful with a voice segment**

In `llamatui/widgets.py`, replace the `StatusBar` class with the stateful version + the pure `render_status` helper:

```python
def render_status(*, model: str, state: str, detail: str, connected: bool, voice: str) -> Text:
    dot = "●" if connected else "○"
    text = Text()
    text.append(f" {dot} ", style="green" if connected else "red")
    text.append(model, style="bold")
    text.append("   ")
    text.append(state, style="cyan")
    if detail:
        text.append("   ")
        text.append(detail, style="dim")
    if voice:
        text.append("   ")
        text.append(voice, style="magenta")
    return text


class StatusBar(Static):
    """A single live line above the prompt. Stateful: it remembers each segment so a gen-driven
    repaint of model/state/detail never erases the independently-owned voice segment."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._model = ""
        self._state = ""
        self._detail = ""
        self._connected = True
        self._voice = ""

    def show(self, *, model=None, state=None, detail=None, connected=None, voice=None) -> None:
        if model is not None:
            self._model = model
        if state is not None:
            self._state = state
        if detail is not None:
            self._detail = detail
        if connected is not None:
            self._connected = connected
        if voice is not None:
            self._voice = voice
        self.update(render_status(
            model=self._model, state=self._state, detail=self._detail,
            connected=self._connected, voice=self._voice,
        ))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_widgets.py -v`
Expected: PASS (5 tests)

- [ ] **Step 6: Run the full suite (the `StatusBar.show` signature changed)**

Run: `python -m pytest -q`
Expected: PASS — existing `_status` callers pass `model`/`state`/`detail`/`connected` explicitly, which the new keyword-only signature still accepts.

- [ ] **Step 7: Commit**

```bash
git add llamatui/widgets.py tests/test_widgets.py
git commit -m "feat: PromptArea dictate binding + stateful StatusBar voice segment"
```

---

### Task 4: Wire config + module construction + banner (no recording yet)

**Files:**
- Modify: `llamatui/app.py` (`Config`, imports, `__init__`, `on_mount`, `on_unmount`)
- Modify: `llamatui/__main__.py` (CLI flags)

**Interfaces:**
- Consumes: `WhisperServer` (Task 1), `Dictation` + `build_recorder` + `State` (Task 2).
- Produces: `LlamaTUI.whisper: WhisperServer | None`, `LlamaTUI.dictation: Dictation | None`, `LlamaTUI.voice_enabled: bool`, and `LlamaTUI._dictation_bg(work, done)`. Wired-but-inert until Task 5 adds the action.

- [ ] **Step 1: Add `Config` fields**

In `llamatui/app.py`, extend `Config.__init__` (add the four params with defaults and assignments):

```python
class Config:
    def __init__(
        self, url, model, system, temperature, max_tokens, top_p,
        thinking_budget=None, db_path=None, web=True, memory=True,
        voice=True, whisper_bin=None, whisper_model=None, whisper_url=None,
    ):
        self.url = url
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.thinking_budget = thinking_budget
        self.db_path = db_path
        self.web = web
        self.memory = memory
        self.voice = voice
        self.whisper_bin = whisper_bin
        self.whisper_model = whisper_model
        self.whisper_url = whisper_url
```

- [ ] **Step 2: Add imports + `__init__` attributes**

In `llamatui/app.py`, add to the imports near the other `from .` lines:

```python
from .dictation import Dictation, State, build_recorder
from .whisper import WhisperServer
```

And in `LlamaTUI.__init__`, after `self.memory: Memory | None = None`:

```python
        self.whisper: WhisperServer | None = None
        self.dictation: Dictation | None = None
        self.voice_enabled = False
        self._cap_timer = None
```

- [ ] **Step 3: Add the background-work seam**

In `llamatui/app.py`, add this method next to `_load_embedder` (it satisfies `Dictation`'s `run_bg` contract: run `work()` off the loop, deliver `done(result)` on the UI thread):

```python
    @work(thread=True, group="dictation")
    def _dictation_bg(self, work, done) -> None:
        result = work()
        self.call_from_thread(done, result)
```

- [ ] **Step 4: Construct the modules in `on_mount`**

In `llamatui/app.py`, in `on_mount`, after the `if self.config.memory:` block and before `self._rebuild_agent()`:

```python
        if self.config.voice:
            self.whisper = WhisperServer(
                bin_path=self.config.whisper_bin,
                model_path=self.config.whisper_model,
                url=self.config.whisper_url,
            )
            recorder = build_recorder()
            if recorder is not None and self.whisper.available():
                self.dictation = Dictation(
                    recorder=recorder,
                    transcriber=self.whisper,
                    run_bg=self._dictation_bg,
                    on_text=self._insert_transcript,
                    on_state=self._voice_state,
                    on_note=self._voice_note,
                )
                self.voice_enabled = True
```

- [ ] **Step 5: Add the voice segment to the startup banner**

In `llamatui/app.py` `on_mount`, change the banner block. After the `mem = ...` line add:

```python
        voice = f"voice [b]{'on' if self.voice_enabled else 'off'}[/]"
```

and append `voice` to the banner string — change `+ f"  ·  {web}  ·  {mem}"` to:

```python
            + f"  ·  {web}  ·  {mem}  ·  {voice}"
```

- [ ] **Step 6: Close the spawned server on unmount**

In `llamatui/app.py` `on_unmount`, before `if self.store is not None:`:

```python
        if self.whisper is not None:
            try:
                self.whisper.close()
            except Exception:
                pass
```

- [ ] **Step 7: Add the CLI flags**

In `llamatui/__main__.py`, after the `--no-memory` argument:

```python
    ap.add_argument("--no-voice", action="store_true", help="disable voice dictation (Ctrl+R)")
    ap.add_argument("--whisper-bin", default=None, help="path to whisper-server (default: whisper/whisper-server.exe, then PATH)")
    ap.add_argument("--whisper-model", default=None, help="path to the whisper ggml model (default: whisper/ggml-small.en.bin)")
    ap.add_argument("--whisper-url", default=None, help="use an already-running whisper-server at this URL instead of spawning one")
```

And in the `Config(...)` call, after `memory=not args.no_memory,`:

```python
        voice=not args.no_voice,
        whisper_bin=args.whisper_bin,
        whisper_model=args.whisper_model,
        whisper_url=args.whisper_url,
```

> Note: Steps 4–5 reference `self._insert_transcript`, `self._voice_state`, `self._voice_note`, which are added in Task 5. The app will import and start, but **do not run it between Task 4 and Task 5** — those handlers must exist first. If you want an intermediate boot check, temporarily stub the three methods with `pass`; Task 5 replaces them. (Subagent-driven execution should treat Tasks 4+5 as a pair.)

- [ ] **Step 8: Commit**

```bash
git add llamatui/app.py llamatui/__main__.py
git commit -m "feat: wire voice config, module construction, banner, unmount"
```

---

### Task 5: Action, status painting, transcript insertion, and the 120 s auto-stop

**Files:**
- Modify: `llamatui/app.py` (action + message handler + voice status helpers + cap timer)

**Interfaces:**
- Consumes: `self.dictation` (Task 4), `PromptArea.Dictate` + `PromptArea.insert_transcript` + `StatusBar.show(voice=...)` (Task 3), `State` (Task 2).
- Produces: `action_dictate`, `on_prompt_area_dictate`, `_insert_transcript`, `_voice_state`, `_voice_note`, `_cap_stop`. No new types.

- [ ] **Step 1: Add the action + Dictate handler + helpers**

In `llamatui/app.py`, add these methods (put them after `action_cancel`, alongside the other `action_*` methods):

```python
    def on_prompt_area_dictate(self, event: PromptArea.Dictate) -> None:
        self.action_dictate()

    def action_dictate(self) -> None:
        if self.dictation is None:
            self._voice_note("voice off — run scripts/get-whisper.ps1 to enable")
            return
        self.dictation.toggle()
        if self.dictation.state is State.RECORDING:
            self._cap_timer = self.set_timer(120.0, self._cap_stop)
        elif self._cap_timer is not None:
            self._cap_timer.stop()
            self._cap_timer = None

    def _cap_stop(self) -> None:
        self._cap_timer = None
        if self.dictation is not None and self.dictation.state is State.RECORDING:
            self._voice_note("recording stopped (max length)")
            self.dictation.toggle()

    def _insert_transcript(self, text: str) -> None:
        prompt = self.query_one("#prompt", PromptArea)
        prompt.insert_transcript(text)
        prompt.focus()

    def _voice_state(self, state) -> None:
        labels = {
            State.IDLE: "",
            State.RECORDING: "🎙 recording",
            State.TRANSCRIBING: "transcribing…",
        }
        self.query_one("#status", StatusBar).show(voice=labels[state])

    def _voice_note(self, msg: str) -> None:
        self.query_one("#status", StatusBar).show(voice=msg)
        self.set_timer(3.0, lambda: self.query_one("#status", StatusBar).show(voice=""))
```

- [ ] **Step 2: Verify the full test suite still passes**

Run: `python -m pytest -q`
Expected: PASS (all suites — no test regressions; the new app methods are exercised manually below).

- [ ] **Step 3: Manual smoke test — voice OFF path (no binary needed)**

Run: `python -m llamatui --whisper-bin /nonexistent --whisper-model /nonexistent`
Expected: app starts; banner ends with `voice off`. Press `Ctrl+R` → status shows `voice off — run scripts/get-whisper.ps1 to enable` for ~3 s, then clears. No crash. `Ctrl+Q` to quit.

- [ ] **Step 4: Manual smoke test — voice ON path (requires Task 6 setup run)**

(After Task 6's `scripts/get-whisper.ps1` has populated `whisper/`.) Run: `python -m llamatui`
Expected: banner ends with `voice on`. Press `Ctrl+R` (status: `🎙 recording`), speak a sentence, press `Ctrl+R` again (status: `transcribing…` → clears), and the transcript appears in the prompt with a leading space if you had text before the cursor. It is **not** sent until you press Enter. Start a reply streaming, then `Ctrl+R` mid-stream and confirm the throughput readout and `🎙 recording` coexist on the status line.

- [ ] **Step 5: Commit**

```bash
git add llamatui/app.py
git commit -m "feat: Ctrl+R dictation action, voice status, insert, 120s auto-stop"
```

---

### Task 6: Setup script + README

**Files:**
- Create: `scripts/get-whisper.ps1`
- Modify: `README.md`

**Interfaces:** none (deliverable + docs). `CONTEXT.md` glossary entries for `WhisperServer` and `Dictation` are already written.

- [ ] **Step 1: Write the fetch script**

Create `scripts/get-whisper.ps1`:

```powershell
# Downloads whisper.cpp's prebuilt Windows CUDA whisper-server (+ its bundled cuBLAS/ggml DLLs)
# and the small.en model into ./whisper/. Match the CUDA build to your driver — these defaults
# target the CUDA 12.x prebuilt release. Bump the version/URLs as new releases land.
param(
    [string]$WhisperVersion = "v1.7.4",
    [string]$ModelUrl = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
)

$ErrorActionPreference = "Stop"
$dir = Join-Path $PSScriptRoot "..\whisper"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

# 1) whisper-server CUDA release (zip with whisper-server.exe + DLLs).
$zipName = "whisper-cublas-12.4.0-bin-x64.zip"   # adjust to the asset name on the chosen release
$releaseUrl = "https://github.com/ggerganov/whisper.cpp/releases/download/$WhisperVersion/$zipName"
$zipPath = Join-Path $dir $zipName
Write-Host "Downloading whisper-server CUDA build: $releaseUrl"
Invoke-WebRequest -Uri $releaseUrl -OutFile $zipPath
Expand-Archive -Path $zipPath -DestinationPath $dir -Force
Remove-Item $zipPath

# 2) Model.
$modelPath = Join-Path $dir "ggml-small.en.bin"
if (-not (Test-Path $modelPath)) {
    Write-Host "Downloading model: $ModelUrl"
    Invoke-WebRequest -Uri $ModelUrl -OutFile $modelPath
}

Write-Host "Done. whisper/ now holds whisper-server.exe, its DLLs, and ggml-small.en.bin."
Write-Host "Enable the capture extra with:  uv sync --extra voice"
```

> The exact release asset name (`$zipName`) varies between whisper.cpp releases — open the release page for `$WhisperVersion`, copy the CUDA (`cublas`) Windows x64 asset name, and set it. The script is intentionally a thin, editable fetcher, not a version oracle.

- [ ] **Step 2: Document it in the README**

In `README.md`, add a `## Voice dictation` section:

```markdown
## Voice dictation (optional)

Press **Ctrl+R** in the prompt to start recording, again to stop; the transcribed text
lands in the input for review and is **never auto-sent**. Transcription runs locally via
whisper.cpp `whisper-server` (CUDA), reusing nothing from the llama stack — it lives in its
own `whisper/` folder.

Setup:

1. Fetch the binary + model:  `pwsh scripts/get-whisper.ps1`
2. Install the capture extra:  `uv sync --extra voice`
3. Run as usual:  `python -m llamatui`  → the banner shows `voice on`.

Flags: `--no-voice` (disable), `--whisper-bin PATH`, `--whisper-model PATH`,
`--whisper-url URL` (point at an already-running whisper-server instead of spawning one).
If `sounddevice`, the binary, the model, or a 16 kHz-capable mic is missing, dictation is
simply off and the banner says so.
```

- [ ] **Step 3: Verify the setup script runs (manual)**

Run: `pwsh scripts/get-whisper.ps1`
Expected: `whisper/` contains `whisper-server.exe`, its DLLs, and `ggml-small.en.bin`. (If the asset name 404s, update `$zipName` per the note above and re-run.) Then complete Task 5 Step 4.

- [ ] **Step 4: Commit**

```bash
git add scripts/get-whisper.ps1 README.md
git commit -m "docs: whisper setup script + README voice section"
```

---

## Self-Review

**Spec coverage** (each design section → task):
- Local CUDA whisper-server, `whisper/` isolation, discover-then-spawn, own-only-spawned → Task 1 (+ ADR).
- Toggle UX, re-entrant no-op, warm-at-start, min-duration guard, 120 s cap, never-auto-send → Task 2 (machine) + Task 5 (timer/action).
- Output normalization (`[BLANK_AUDIO]`) → Task 1 `_clean_transcript`.
- 16 kHz mono capture, honest failure, default device, `build_recorder` feature-detect → Task 2.
- `Ctrl+R` binding + `Dictate` message + `insert_transcript` spacing → Task 3 + Task 5.
- Dedicated `voice` status segment, stateful `StatusBar` → Task 3 (+ painted in Task 5).
- Config fields + CLI flags (`--no-voice`, `--whisper-bin`, `--whisper-model`, `--whisper-url`) → Task 4.
- Module construction, banner segment, `on_unmount` close → Task 4.
- `[voice]` extra → Task 2. Setup script + README → Task 6. Glossary → done.
- Error table rows (mic 16 kHz fail → `build_recorder` returns None / off; spawn-health fail → `_warm_done` note; HTTP fail → `_transcribe_done` note; empty → no insert; cap → auto-stop) → Tasks 1/2/5.

**Placeholder scan:** every code step contains complete code; the two genuinely environment-specific values (whisper release asset name; CUDA version) are flagged as editable with instructions, not left as silent TODOs.

**Type consistency:** `State` members (`IDLE`/`RECORDING`/`TRANSCRIBING`), `WhisperServer.{available,ensure_running,transcribe,close}`, `Dictation.toggle`/`.state`, `run_bg(work, done)`, `build_recorder`, `insert_transcript`, `render_status`, and the `StatusBar.show(voice=...)` keyword are used identically across Tasks 1–5.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-voice-dictation.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. (Note: Tasks 4 and 5 are a dependency pair — the same subagent should do both, or review them together, since Task 4 references handlers defined in Task 5.)
2. **Inline Execution** — execute tasks in this session with checkpoints for review.

Which approach?
