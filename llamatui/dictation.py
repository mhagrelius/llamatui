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
