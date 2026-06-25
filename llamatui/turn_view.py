"""TurnView — folds a TurnState into one AssistantTurn. The mirror of TurnStream.

TurnStream folds the wire into state (one turn in, structured state out); TurnView folds that
state into one assistant turn's widget. Everything the streaming worker used to do inline —
the render throttle, the live tok/s estimate, tool-chip bookkeeping, the thinking-pane settle
policy, and the replay path — lives here behind a narrow interface. The worker (and the replay
path) speak only to TurnView; TurnView speaks only to the widget, through the ``TurnWidget`` seam.
Framework-free and clock-injected like TurnStream, so a spy widget + a fake clock test the whole
state→widget mapping with no Textual.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Protocol

from .turn import TurnState, strip_tool_noise, WRITING

RENDER_INTERVAL = 0.06

# The persisted metrics blob shape lives here — one module owns both ends (write + parse), so the
# convention can't drift between the worker that saves it and the replay that reads it.
_METRICS_KEY = "line"


def metrics_blob(line: str) -> dict[str, str]:
    """The dict persisted with a finished turn (storage serializes it to JSON)."""
    return {_METRICS_KEY: line}


def _parse_metrics_blob(blob: str | None) -> str | None:
    if not blob:
        return None
    try:
        return json.loads(blob).get(_METRICS_KEY)
    except Exception:
        return None


def _short_result(text: str | None, cap: int = 64) -> str | None:
    """First line of a tool result, trimmed for the one-line tool-call chip."""
    if not text:
        return None
    first = text.strip().splitlines()[0].strip()
    return first if len(first) <= cap else first[: cap - 1].rstrip() + "…"


class TurnWidget(Protocol):
    """The mechanical setter surface TurnView drives (AssistantTurn in prod, a spy in tests)."""
    def set_reasoning(self, text: str) -> None: ...
    def set_answer(self, text: str) -> None: ...
    def add_tool_call(self, call_id: str, name: str) -> None: ...
    def update_tool(self, call_id: str, label: str, done: bool = ..., failed: bool = ...) -> None: ...
    def collapse_thinking(self) -> None: ...
    def drop_thinking(self) -> None: ...
    def set_think_title(self, title: str) -> None: ...
    def set_metrics(self, line: str, classes: str = ...) -> None: ...


class TurnView:
    def __init__(
        self,
        widget: TurnWidget,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_status: Callable[[str, float], None] = lambda phase, rate: None,
        interval: float = RENDER_INTERVAL,
    ) -> None:
        self._w = widget
        self._clock = clock
        self._on_status = on_status
        self._interval = interval
        self._t0 = clock()
        self._last_render: float | None = None
        self._collapsed = False
        self._seen_calls: set[str] = set()

    # ---- live streaming --------------------------------------------------
    def reflect(self, state: TurnState, force: bool = False) -> None:
        # The thinking pane collapses the instant the turn starts writing — unthrottled, so a
        # throttled render can't delay it past the phase change.
        if not self._collapsed and state.phase == WRITING:
            self._w.collapse_thinking()
            self._collapsed = True
        now = self._clock()
        if not force and self._last_render is not None and now - self._last_render < self._interval:
            return
        self._last_render = now
        if state.reasoning:
            self._w.set_reasoning(state.reasoning)
        if state.answer:
            self._w.set_answer(strip_tool_noise(state.answer))
        self._reflect_tools(state)
        self._emit_status(state)

    def _emit_status(self, state: TurnState) -> None:
        # A crude live throughput estimate (chars/4 ≈ tokens) for the status bar; the authoritative
        # token accounting is metrics.extract at finalize. The App owns the StatusBar widget.
        chars = len(state.answer) if state.answer else len(state.reasoning)
        generating = max(1e-6, (self._clock() - self._t0) - (state.ttft_s or 0.0))
        self._on_status(state.phase, (chars // 4) / generating)

    # ---- finalize --------------------------------------------------------
    def finalize(self, state: TurnState, metrics_line: str) -> None:
        """Settle the completed turn: the thinking pane (dropped, or titled with its reasoning
        token count and collapsed) and the metrics line. Assumes the final reflect already ran."""
        rt = (state.usage_details or {}).get("reasoning_output_token_count")
        title = f"Thinking ({rt:,} tokens)" if rt else "Thinking"
        self._settle(state.has_reasoning, title, metrics_line)

    def _settle(self, reasoning_present: bool, title: str, metrics_line: str | None) -> None:
        if reasoning_present:
            self._w.set_think_title(title)
            self._w.collapse_thinking()
        else:
            self._w.drop_thinking()
        if metrics_line:
            self._w.set_metrics(metrics_line)

    # ---- replay (persisted turn, no streaming) ---------------------------
    def load_saved(self, *, answer: str, reasoning: str | None, metrics: str | None) -> None:
        """Populate a turn from a stored row. Shares the thinking-pane/metrics policy with
        finalize; the metrics line is the plain title (no live reasoning-token count)."""
        if reasoning:
            self._w.set_reasoning(reasoning)
        self._w.set_answer(answer)
        self._settle(bool(reasoning), "Thinking", _parse_metrics_blob(metrics))

    # ---- error -----------------------------------------------------------
    def error(self, exc: BaseException) -> None:
        self._w.set_metrics(f"⚠ {type(exc).__name__}: {exc}", classes="error")

    def _reflect_tools(self, state: TurnState) -> None:
        for call in state.tool_calls:
            label = call.name + (f"  «{call.query}»" if call.query else "")
            if call.call_id not in self._seen_calls:
                self._seen_calls.add(call.call_id)
                self._w.add_tool_call(call.call_id, call.name)
            if call.done:
                # Show the tool's actual result, so "done" can't hide a no-op or error.
                status = "failed" if call.failed else (_short_result(call.result) or "done")
                self._w.update_tool(call.call_id, f"{label}  · {status}", done=True, failed=call.failed)
            else:
                self._w.update_tool(call.call_id, label)
