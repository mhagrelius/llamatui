"""VoiceInput's interface is the test surface: it maps a Ctrl+R key stream to Dictation verbs
per the voice mode, owning the toggle debounce, the two-phase hold-release gap, the shared 120 s
cap, and the re-arm on a mid-recording mode change. Framework-free: the clock and the interval
scheduler are injected, so the whole key-stream -> verb mapping runs synchronously with fakes.
"""

from __future__ import annotations

from llamatui.dictation import Dictation, State, SAMPLE_RATE, MIN_SAMPLES
from llamatui.settings import VoiceMode
from llamatui.voice import VoiceInput


# ---- doubles -------------------------------------------------------------
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


class FakeClock:
    """An injected monotonic clock the test advances by hand."""
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


class FakeScheduler:
    """Stands in for Textual's set_interval. Records the active (interval, callback) and hands
    back a zero-arg cancel. ``tick()`` fires the callback as a real interval would."""
    def __init__(self):
        self.interval = None
        self.callback = None
        self.cancelled = False
        self.arms = 0
    def __call__(self, interval, callback):
        self.interval = interval
        self.callback = callback
        self.cancelled = False
        self.arms += 1
        return self._cancel
    def _cancel(self):
        self.cancelled = True
        self.callback = None
    def tick(self):
        if self.callback is not None:
            self.callback()


def _pcm(n_samples):
    return b"\x00\x00" * n_samples


def _make(mode=VoiceMode.TOGGLE, *, pcm=None, d=0.5):
    pcm = _pcm(SAMPLE_RATE) if pcm is None else pcm
    rec = FakeRecorder(pcm=pcm)
    stt = FakeTranscriber(text="ship it")
    texts, states, notes = [], [], []
    dictation = Dictation(
        recorder=rec, transcriber=stt, run_bg=run_sync,
        on_text=texts.append, on_state=states.append, on_note=notes.append,
    )
    clock = FakeClock()
    sched = FakeScheduler()
    v = VoiceInput(
        dictation, sched, mode=mode, d=d, clock=clock, on_note=notes.append,
    )
    return v, dictation, clock, sched, texts, notes


# ---- toggle mode ---------------------------------------------------------
def test_toggle_first_key_starts_recording():
    v, dictation, *_ = _make(VoiceMode.TOGGLE)
    v.key()
    assert dictation.state is State.RECORDING


def test_toggle_rapid_repeat_is_swallowed_but_deliberate_press_stops():
    v, dictation, clock, sched, texts, _ = _make(VoiceMode.TOGGLE)
    v.key()                                # t=0: start
    assert dictation.state is State.RECORDING
    clock.advance(0.05)
    v.key()                                # auto-repeat within window: swallowed
    assert dictation.state is State.RECORDING
    assert texts == []
    clock.advance(3.0)
    v.key()                                # deliberate press, seconds later: stops + transcribes
    assert dictation.state is State.IDLE
    assert texts == ["ship it"]


def test_toggle_cap_auto_stops_after_120s():
    v, dictation, clock, sched, texts, notes = _make(VoiceMode.TOGGLE)
    v.key()                                # start at t=0
    assert dictation.state is State.RECORDING
    assert sched.callback is not None      # poll armed while recording
    clock.advance(119.0)
    sched.tick()                           # under the cap: still recording
    assert dictation.state is State.RECORDING
    clock.advance(1.5)                     # past 120 s
    sched.tick()
    assert dictation.state is State.IDLE   # auto-stopped + transcribed
    assert any("max length" in n for n in notes)
    assert sched.cancelled is True         # poll cancelled — no leaked timer


# ---- hold mode -----------------------------------------------------------
def test_hold_first_key_starts_and_arms_poll():
    v, dictation, clock, sched, *_ = _make(VoiceMode.HOLD, d=0.5)
    v.key()
    assert dictation.state is State.RECORDING
    assert sched.callback is not None


def test_hold_release_before_first_repeat_waits_d_plus_margin():
    v, dictation, clock, sched, texts, _ = _make(VoiceMode.HOLD, d=0.5)
    v.key()                                 # t=0: a single keydown, no repeat seen yet
    clock.advance(0.5 + 0.30 - 0.02)        # just under D + margin
    sched.tick()
    assert dictation.state is State.RECORDING
    clock.advance(0.04)                     # now just over
    sched.tick()
    assert dictation.state is State.IDLE    # release inferred → stop → transcribe
    assert texts == ["ship it"]
    assert sched.cancelled is True


def test_hold_release_after_repeat_uses_short_gap_independent_of_d():
    v, dictation, clock, sched, *_ = _make(VoiceMode.HOLD, d=0.5)
    v.key()                                 # t=0: start
    clock.advance(0.5)
    v.key()                                 # first auto-repeat → confirms the burst is live
    sched.tick()
    assert dictation.state is State.RECORDING
    clock.advance(0.20 + 0.02)              # short active gap, NOT D + margin
    sched.tick()
    assert dictation.state is State.IDLE


def test_hold_long_burst_stays_alive():
    v, dictation, clock, sched, *_ = _make(VoiceMode.HOLD, d=0.5)
    v.key()                                 # start
    clock.advance(0.5)
    for _ in range(50):                     # a long burst of repeats ~33 ms apart
        v.key()
        sched.tick()
        assert dictation.state is State.RECORDING
        clock.advance(0.033)


# ---- mode change ---------------------------------------------------------
def test_set_mode_mid_recording_cancels_poll_and_rearms():
    v, dictation, clock, sched, texts, _ = _make(VoiceMode.HOLD, d=0.5)
    v.key()                                 # recording in hold mode
    assert dictation.state is State.RECORDING
    v.set_mode(VoiceMode.TOGGLE)            # switch mid-recording
    assert dictation.state is State.IDLE    # in-flight recording discarded, not transcribed
    assert texts == []
    assert sched.cancelled is True          # poll cancelled — no leaked timer
    v.key()                                 # re-arms cleanly under the new mode
    assert dictation.state is State.RECORDING
