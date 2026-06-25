"""TurnView's interface is the test surface: it folds a TurnState into one AssistantTurn (the
mirror of TurnStream, which folds the wire into the state). Throttle, the live tok/s estimate,
tool-chip bookkeeping, the thinking-pane settle policy, and the replay path all live here, driven
through a narrow TurnWidget seam. A spy widget + an injected clock keep it framework-free — no
Textual, no server.
"""

from __future__ import annotations

from llamatui.turn import TurnState, ToolCall, WRITING, THINKING
from llamatui.turn_view import TurnView


class SpyWidget:
    """Records the mechanical setter calls TurnView makes (the TurnWidget seam in prod is
    AssistantTurn). Each call appends to ``calls`` so tests assert on the sequence."""
    def __init__(self):
        self.calls = []
        self.answer = None
        self.reasoning = None
    def set_reasoning(self, text):
        self.reasoning = text
        self.calls.append(("set_reasoning", text))
    def set_answer(self, text):
        self.answer = text
        self.calls.append(("set_answer", text))
    def add_tool_call(self, call_id, name):
        self.calls.append(("add_tool_call", call_id, name))
    def update_tool(self, call_id, label, done=False, failed=False):
        self.calls.append(("update_tool", call_id, label, done, failed))
    def collapse_thinking(self):
        self.calls.append(("collapse_thinking",))
    def drop_thinking(self):
        self.calls.append(("drop_thinking",))
    def set_think_title(self, title):
        self.calls.append(("set_think_title", title))
    def set_metrics(self, line, classes=""):
        self.calls.append(("set_metrics", line, classes))


class FakeClock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


def _make(**kw):
    widget = SpyWidget()
    clock = FakeClock()
    status = []
    view = TurnView(widget, clock=clock, on_status=lambda phase, rate: status.append((phase, rate)), **kw)
    return view, widget, clock, status


def _names(widget):
    return [c[0] for c in widget.calls]


def test_reflect_pushes_answer_to_widget():
    view, widget, *_ = _make()
    view.reflect(TurnState(answer="hello", phase=WRITING), force=True)
    assert widget.answer == "hello"


def test_reflect_throttles_until_interval_elapses():
    view, widget, clock, _ = _make(interval=0.06)
    view.reflect(TurnState(answer="a", phase=WRITING))     # first render always goes through
    assert widget.answer == "a"
    clock.advance(0.03)
    view.reflect(TurnState(answer="ab", phase=WRITING))    # within interval → swallowed
    assert widget.answer == "a"
    clock.advance(0.04)                                    # past interval since last render
    view.reflect(TurnState(answer="abc", phase=WRITING))   # renders
    assert widget.answer == "abc"


def test_force_renders_even_within_interval():
    view, widget, clock, _ = _make(interval=0.06)
    view.reflect(TurnState(answer="a", phase=WRITING))
    clock.advance(0.01)
    view.reflect(TurnState(answer="ab", phase=WRITING), force=True)
    assert widget.answer == "ab"


def test_collapse_thinking_fires_once_on_writing_and_is_unthrottled():
    view, widget, clock, _ = _make(interval=0.06)
    view.reflect(TurnState(reasoning="mulling", phase=THINKING))
    assert "collapse_thinking" not in _names(widget)
    clock.advance(0.001)                                   # inside the throttle window
    view.reflect(TurnState(reasoning="mulling", answer="a", phase=WRITING))
    assert _names(widget).count("collapse_thinking") == 1  # collapses despite being throttled
    clock.advance(0.10)
    view.reflect(TurnState(answer="ab", phase=WRITING))
    assert _names(widget).count("collapse_thinking") == 1  # never re-collapses


def test_tool_chip_added_once_then_marked_done_with_result():
    view, widget, clock, _ = _make(interval=0.06)
    call = ToolCall(call_id="c1", name="web_search", args='{"query": "weather"}')
    view.reflect(TurnState(tool_calls=[call], phase=THINKING), force=True)
    assert ("add_tool_call", "c1", "web_search") in widget.calls
    open_updates = [c for c in widget.calls if c[0] == "update_tool" and c[3] is False]
    assert open_updates and "weather" in open_updates[-1][2]   # in-flight label carries the query

    call.done = True
    call.result = "sunny, 21°C\nsecond line"
    clock.advance(0.10)
    view.reflect(TurnState(tool_calls=[call], phase=WRITING), force=True)
    assert sum(1 for c in widget.calls if c[0] == "add_tool_call") == 1   # never re-added
    done = [c for c in widget.calls if c[0] == "update_tool" and c[3] is True]
    assert done and "sunny" in done[-1][1 + 1] and done[-1][4] is False   # first-line result, not failed


def test_tool_chip_failed_marks_failed():
    view, widget, *_ = _make()
    call = ToolCall(call_id="c2", name="web_search", done=True, failed=True, result="failed: boom")
    view.reflect(TurnState(tool_calls=[call], phase=WRITING), force=True)
    done = [c for c in widget.calls if c[0] == "update_tool" and c[3] is True]
    assert done and done[-1][4] is True


def test_reflect_emits_status_with_phase_and_live_rate():
    view, widget, clock, status = _make(interval=0.06)   # view built at t=0 → its t0 is 0.0
    clock.advance(1.0)
    view.reflect(TurnState(answer="x" * 40, phase=WRITING, ttft_s=0.0), force=True)
    assert status                                        # fires on render
    phase, rate = status[-1]
    assert phase == WRITING
    assert 8 <= rate <= 12                               # 40 chars ≈ 10 tokens over ~1.0 s


def test_status_does_not_fire_when_throttled():
    view, widget, clock, status = _make(interval=0.06)
    view.reflect(TurnState(answer="a", phase=WRITING))   # renders + emits
    clock.advance(0.01)
    view.reflect(TurnState(answer="ab", phase=WRITING))  # throttled → no emit
    assert len(status) == 1


# ---- finalize ------------------------------------------------------------
def test_finalize_with_reasoning_titles_pane_and_sets_metrics():
    view, widget, *_ = _make()
    st = TurnState(reasoning="deep", answer="ans", phase=WRITING,
                   usage_details={"reasoning_output_token_count": 1234})
    view.finalize(st, "12 tok/s · 1.2s")
    assert ("set_think_title", "Thinking (1,234 tokens)") in widget.calls
    assert ("collapse_thinking",) in widget.calls
    assert ("set_metrics", "12 tok/s · 1.2s", "") in widget.calls
    assert "drop_thinking" not in _names(widget)


def test_finalize_without_reasoning_drops_pane():
    view, widget, *_ = _make()
    view.finalize(TurnState(answer="ans", phase=WRITING), "line")
    assert ("drop_thinking",) in widget.calls
    assert "set_think_title" not in _names(widget)


# ---- replay --------------------------------------------------------------
def test_load_saved_parses_blob_and_replays_shared_settle():
    view, widget, *_ = _make()
    view.load_saved(answer="saved answer", reasoning="saved reasoning", metrics='{"line": "9 tok/s"}')
    assert ("set_reasoning", "saved reasoning") in widget.calls
    assert ("set_answer", "saved answer") in widget.calls
    assert ("set_think_title", "Thinking") in widget.calls       # plain title — no token count on replay
    assert ("collapse_thinking",) in widget.calls
    assert ("set_metrics", "9 tok/s", "") in widget.calls


def test_load_saved_without_reasoning_or_metrics_drops_pane():
    view, widget, *_ = _make()
    view.load_saved(answer="a", reasoning=None, metrics=None)
    assert ("drop_thinking",) in widget.calls
    assert ("set_answer", "a") in widget.calls
    assert "set_metrics" not in _names(widget)
    assert "set_think_title" not in _names(widget)


def test_load_saved_tolerates_bad_blob():
    view, widget, *_ = _make()
    view.load_saved(answer="a", reasoning=None, metrics="not json")
    assert "set_metrics" not in _names(widget)                   # bad blob → no line, no crash


def test_metrics_blob_round_trips_through_load_saved():
    import json
    from llamatui.turn_view import metrics_blob
    view, widget, *_ = _make()
    view.load_saved(answer="a", reasoning=None, metrics=json.dumps(metrics_blob("7 tok/s")))
    assert ("set_metrics", "7 tok/s", "") in widget.calls


# ---- error ---------------------------------------------------------------
def test_error_sets_error_styled_metrics_line():
    view, widget, *_ = _make()
    view.error(ValueError("boom"))
    assert ("set_metrics", "⚠ ValueError: boom", "error") in widget.calls
