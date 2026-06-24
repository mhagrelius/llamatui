"""Leading-edge debounce for the dictate action: a held Ctrl+R produces terminal key
auto-repeat (many events ~30-50 ms apart); the whole burst must collapse to one toggle.
The decision is a pure, clock-injected helper so it's testable without a running App."""

from llamatui.app import _Debouncer


def test_first_event_fires():
    d = _Debouncer(0.5)
    assert d.should_fire(0.0) is True


def test_rapid_repeats_are_swallowed():
    d = _Debouncer(0.5)
    assert d.should_fire(0.0) is True       # initial press
    assert d.should_fire(0.05) is False     # auto-repeat
    assert d.should_fire(0.10) is False
    assert d.should_fire(0.40) is False


def test_held_burst_keeps_collapsing_past_the_window():
    # Each event refreshes the timer, so a continuous burst never re-fires even though
    # absolute time exceeds the window — this is what stops mid-hold flicker.
    d = _Debouncer(0.5)
    assert d.should_fire(0.0) is True
    for t in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
        assert d.should_fire(t) is False
    assert d.should_fire(1.20) is True      # quiet gap > window → a fresh press fires


def test_spaced_deliberate_presses_both_fire():
    d = _Debouncer(0.5)
    assert d.should_fire(0.0) is True       # press to start
    assert d.should_fire(3.0) is True       # press to stop, seconds later
