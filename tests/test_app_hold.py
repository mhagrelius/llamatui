"""The hold-to-talk decision is a pure, clock-injected helper (like _Debouncer): it maps a
Ctrl+R auto-repeat burst to start/stop by inferring 'release' from a gap in the burst. Two-phase
gap — D+margin before the first repeat, a short gap after. See ADR-0002."""

from llamatui.app import (
    HOLD_INITIAL_MARGIN_S, HOLD_RELEASE_GAP_ACTIVE_S, _HoldController, keyboard_initial_delay_s,
)

D = 0.5  # injected initial delay for deterministic tests


def test_first_key_starts_and_sets_recording():
    h = _HoldController(D)
    assert h.recording is False
    assert h.on_key(0.0) is True            # first press → start
    assert h.recording is True
    assert h.on_key(0.0) is False           # subsequent keys never re-start


def test_before_first_repeat_waits_D_plus_margin():
    h = _HoldController(D)
    h.on_key(0.0)
    # only one keydown so far (no repeat seen): must wait D + margin before stopping
    assert h.expired(D + HOLD_INITIAL_MARGIN_S - 0.01) is False
    assert h.expired(D + HOLD_INITIAL_MARGIN_S + 0.01) is True
    assert h.recording is False


def test_repeat_confirmed_then_stops_on_short_gap_independent_of_D():
    h = _HoldController(D)
    h.on_key(0.0)               # start
    h.on_key(D)                 # first auto-repeat → confirms 'repeating'
    # now the active (short) gap applies, NOT D+margin
    assert h.expired(D + HOLD_RELEASE_GAP_ACTIVE_S - 0.01) is False
    assert h.expired(D + HOLD_RELEASE_GAP_ACTIVE_S + 0.01) is True


def test_long_hold_stream_stays_alive():
    h = _HoldController(D)
    h.on_key(0.0)
    t = D
    for _ in range(50):         # a long burst of repeats ~33 ms apart
        h.on_key(t)
        assert h.expired(t + 0.01) is False
        t += 0.033


def test_expired_is_false_when_not_recording():
    h = _HoldController(D)
    assert h.expired(100.0) is False


def test_keyboard_initial_delay_is_sane_float():
    v = keyboard_initial_delay_s()
    assert isinstance(v, float)
    assert 0.2 <= v <= 1.0


def test_reset_rearms_controller():
    h = _HoldController(D)
    h.on_key(0.0)                 # start
    assert h.recording is True
    h.reset()
    assert h.recording is False
    assert h.on_key(1.0) is True  # next press starts fresh
