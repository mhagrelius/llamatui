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
