"""VoiceInput — maps the Ctrl+R key stream to Dictation verbs per the voice mode.

The App owns no dictation timer lifecycle: it forwards one ``key()`` per Ctrl+R and pushes mode
changes via ``set_mode()``. Everything else — the toggle debounce, the two-phase hold-release gap
(ADR-0002), the shared 120 s cap, and the re-arm on a mid-recording mode change — lives here,
behind a two-method interface. Framework-free and deterministic: the clock and the interval
scheduler are injected (mirroring Dictation's recorder/transcriber/run_bg seams), so the whole
mapping is unit-tested with fakes and no Textual.
"""

from __future__ import annotations

import ctypes
import time
from typing import Callable

from .dictation import Dictation, State
from .settings import VoiceMode

# A periodic-wakeup seam, in the style of dictation.RunBg: schedule ``callback`` every ``interval``
# seconds and return a zero-arg cancel. The App passes a Textual ``set_interval`` adapter; tests
# pass a fake that fires on demand.
Cancel = Callable[[], None]
ScheduleInterval = Callable[[float, Callable[[], None]], Cancel]

# Holding Ctrl+R makes the terminal auto-repeat the key (~30-50 ms apart), which would otherwise
# toggle dictation on/off rapidly (flicker). A leading-edge debounce fires on the first press and
# swallows the rest of the burst; every event refreshes the timer, so a continuous hold never
# re-fires until there's a quiet gap longer than the window.
DICTATE_DEBOUNCE_S = 0.5

# The recording auto-stop. The poll runs while recording (in both modes) and stops dictation
# once this deadline passes; dictation.MAX_SAMPLES is the independent defensive truncation floor.
CAP_SECONDS = 120.0
POLL_INTERVAL_S = 0.05


class _Debouncer:
    def __init__(self, window_s: float) -> None:
        self._window = window_s
        self._last: float | None = None

    def should_fire(self, now: float) -> bool:
        fire = self._last is None or (now - self._last) >= self._window
        self._last = now
        return fire


_DEFAULT_KEY_DELAY_S = 0.5

# Hold-to-talk gaps. Before the first auto-repeat we must wait the OS initial-repeat delay D plus a
# margin (a held key is silent until repeat begins at ~D). Once a repeat confirms the burst is live,
# a short gap means release — crisp and independent of D. See ADR-0002.
HOLD_INITIAL_MARGIN_S = 0.30
HOLD_RELEASE_GAP_ACTIVE_S = 0.20


def keyboard_initial_delay_s() -> float:
    """OS initial key-repeat delay in seconds, or a safe default if unavailable.
    Windows SPI_GETKEYBOARDDELAY returns 0-3 → 250/500/750/1000 ms."""
    SPI_GETKEYBOARDDELAY = 0x0016
    try:
        value = ctypes.c_int()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETKEYBOARDDELAY, 0, ctypes.byref(value), 0
        )
        if ok and 0 <= value.value <= 3:
            return 0.25 * (value.value + 1)
    except Exception:  # pragma: no cover - non-Windows / restricted env falls back
        pass
    return _DEFAULT_KEY_DELAY_S


class _HoldController:
    """Maps a Ctrl+R auto-repeat burst to record start/stop, inferring 'release' from a gap in the
    burst (terminals expose no key-release; see ADR-0002). Pure and clock-injected: VoiceInput feeds
    it key events and poll ticks and acts on the returned signals."""

    def __init__(self, initial_delay_s: float) -> None:
        self._before_gap = initial_delay_s + HOLD_INITIAL_MARGIN_S
        self._active_gap = HOLD_RELEASE_GAP_ACTIVE_S
        self._recording = False
        self._repeating = False
        self._last_key = 0.0

    def reset(self) -> None:
        """Re-arm: clear recording/repeating so the next on_key starts a fresh recording."""
        self._recording = False
        self._repeating = False

    @property
    def recording(self) -> bool:
        return self._recording

    def on_key(self, now: float) -> bool:
        """A Ctrl+R event. Returns True only on the first press (caller should start recording);
        later events are auto-repeat and confirm the burst is live."""
        self._last_key = now
        if not self._recording:
            self._recording = True
            self._repeating = False
            return True
        self._repeating = True
        return False

    def expired(self, now: float) -> bool:
        """Poll tick. Returns True once the release gap has elapsed (caller should stop)."""
        if not self._recording:
            return False
        gap = self._active_gap if self._repeating else self._before_gap
        if now - self._last_key > gap:
            self._recording = False
            self._repeating = False
            return True
        return False


class VoiceInput:
    def __init__(
        self,
        dictation: Dictation,
        schedule_interval: ScheduleInterval,
        *,
        mode: VoiceMode,
        d: float,
        clock: Callable[[], float] = time.monotonic,
        on_note: Callable[[str], None] = lambda m: None,
    ) -> None:
        self._dictation = dictation
        self._schedule = schedule_interval
        self._mode = mode
        self._d = d
        self._clock = clock
        self._on_note = on_note
        self._debounce = _Debouncer(DICTATE_DEBOUNCE_S)
        self._hold = _HoldController(d)
        self._cancel_poll: Cancel | None = None
        self._cap_deadline: float | None = None

    # ---- public verbs ---------------------------------------------------
    def key(self) -> None:
        """The dictate key fired."""
        if self._mode is VoiceMode.TOGGLE:
            # Collapse held-key auto-repeat to a single toggle.
            if self._debounce.should_fire(self._clock()):
                self._dictation.toggle()
                self._sync_poll()
        else:  # HOLD: first press starts; later events are auto-repeat that keep the burst alive.
            if self._hold.on_key(self._clock()):
                self._dictation.start()
                self._arm_poll()

    def set_mode(self, mode: VoiceMode) -> None:
        """Switch toggle/hold. Discards any in-flight recording and re-arms, so the next key
        starts clean under the new mode."""
        self._dictation.cancel()
        self._disarm_poll()                       # cancel the poll + reset the hold oracle
        self._debounce = _Debouncer(DICTATE_DEBOUNCE_S)
        self._mode = mode

    # ---- the poll: cap (both modes) + hold-release ----------------------
    def _sync_poll(self) -> None:
        """Run the poll iff recording; (dis)arm the cap to match."""
        if self._dictation.state is State.RECORDING:
            self._arm_poll()
        else:
            self._disarm_poll()

    def _arm_poll(self) -> None:
        if self._cancel_poll is None:
            self._cap_deadline = self._clock() + CAP_SECONDS
            self._cancel_poll = self._schedule(POLL_INTERVAL_S, self._poll)

    def _disarm_poll(self) -> None:
        if self._cancel_poll is not None:
            self._cancel_poll()
            self._cancel_poll = None
        self._cap_deadline = None
        self._hold.reset()              # keep the hold oracle re-armed whenever the poll ends

    def _poll(self) -> None:
        now = self._clock()
        if self._cap_deadline is not None and now >= self._cap_deadline:
            if self._dictation.state is State.RECORDING:
                self._on_note("recording stopped (max length)")
                self._dictation.stop()
            self._disarm_poll()
            return
        if self._mode is VoiceMode.HOLD and self._hold.expired(now):
            self._dictation.stop()      # release inferred from the gap in the auto-repeat burst
            self._disarm_poll()
