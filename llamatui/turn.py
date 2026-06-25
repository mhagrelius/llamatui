"""Accumulate one streamed assistant turn into structured, testable state.

This is the single place that knows how a llama-server turn arrives over the Agent
Framework stream: the content-type vocabulary (``text_reasoning``, ``text``,
``function_call``, ``function_result``, ``usage``) and where llama.cpp hides its
non-standard ``timings`` block on the raw chunk. The Textual worker feeds updates in and
reflects the resulting :class:`TurnState` into widgets; tests feed recorded updates and
assert the state, with no App and no live server.

Deepening note: this folds together two former smears — the streaming state machine that
lived inline in ``app.generate()`` and the wire-shape knowledge that was split across the
worker (``getattr`` dispatch, ``raw_representation``/``model_extra`` digging). Both now have
one home behind one interface.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Phases a turn moves through; the UI reflects these in the status bar.
THINKING = "thinking"
SEARCHING = "searching"
WRITING = "writing"

_QUERY_RE = re.compile(r'"query"\s*:\s*"([^"]*)"')

# A model may leak a tool call into its answer as plain text (e.g. "<tool_call><function=
# remember>...") instead of a structured call. That never executes, so strip it from the
# visible/persisted answer. Match whole blocks first, then any leftover stray tags.
_TOOL_BLOCK_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_TAG_RE = re.compile(r"</?tool_call\s*>|</?function[^>]*>|</?parameter[^>]*>", re.IGNORECASE)


def extract_query(args: str) -> str | None:
    """Pull a ``"query"`` value out of a (possibly partial) tool-call argument blob.

    Tool arguments stream in token by token, so the JSON is often incomplete; a forgiving
    regex beats a real parser here. Returns ``None`` when no query is visible yet.
    """
    m = _QUERY_RE.search(args or "")
    return m.group(1) if m else None


def strip_tool_noise(text: str) -> str:
    """Remove tool-call markup a model leaked into answer text (it never ran)."""
    if "<tool_call" not in text and "<function=" not in text and "<parameter=" not in text:
        return text
    text = _TOOL_BLOCK_RE.sub("", text)
    text = _TOOL_TAG_RE.sub("", text)
    return text.strip()


def _stringify_result(res: Any) -> str | None:
    if res is None or isinstance(res, str):
        return res
    text = getattr(res, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(res, (list, tuple)):
        parts = [r if isinstance(r, str) else getattr(r, "text", None) for r in res]
        joined = " ".join(p for p in parts if isinstance(p, str))
        if joined:
            return joined
    return str(res)


def _result_text(c: Any) -> tuple[str | None, bool]:
    """Pull a (text, failed) pair off a function_result content."""
    exc = getattr(c, "exception", None)
    if exc:
        return (f"failed: {exc}", True)
    res = getattr(c, "result", None)
    if res is None:
        res = getattr(c, "output", None)
    return (_stringify_result(res), False)


@dataclass
class ToolCall:
    """One tool invocation the model made during the turn."""

    call_id: str
    name: str
    args: str = ""
    done: bool = False
    result: str | None = None   # the tool's returned text, surfaced so "done" can't lie
    failed: bool = False

    @property
    def query(self) -> str | None:
        return extract_query(self.args)


@dataclass
class TurnState:
    """Everything we know about the turn so far. Pure data — safe to assert on."""

    reasoning: str = ""
    answer: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage_details: dict[str, Any] | None = None
    timings: dict[str, Any] | None = None
    ttft_s: float | None = None
    phase: str = THINKING

    @property
    def has_reasoning(self) -> bool:
        return bool(self.reasoning.strip())

    @property
    def has_answer(self) -> bool:
        return bool(self.answer.strip())


def _content_type(c: Any) -> str | None:
    return getattr(c, "type", None)


def _extract_timings(usage_content: Any) -> dict[str, Any] | None:
    """Dig llama.cpp's ``timings`` block off the raw chunk left on a usage content."""
    ch = getattr(usage_content, "raw_representation", None)
    if ch is None:
        return None
    timings = getattr(ch, "timings", None)
    if timings is None and getattr(ch, "model_extra", None):
        timings = ch.model_extra.get("timings")
    return timings


class TurnStream:
    """Folds a stream of Agent Framework update contents into a :class:`TurnState`.

    Feed it with :meth:`ingest` (one ``ChatResponseUpdate`` at a time) and read
    :attr:`state` after each call. The only impure input is the clock, injected so
    ``ttft_s`` and :meth:`elapsed` are deterministic under test.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._t0 = clock()
        self._calls: dict[str, ToolCall] = {}
        self._current: str | None = None
        self._out_sum = 0
        self._reason_sum = 0
        self.state = TurnState()

    # ---- timing ----------------------------------------------------------
    def _mark_first_token(self) -> None:
        if self.state.ttft_s is None:
            self.state.ttft_s = self._clock() - self._t0

    def elapsed(self) -> float:
        return self._clock() - self._t0

    # ---- ingestion -------------------------------------------------------
    def ingest(self, update: Any) -> None:
        for c in getattr(update, "contents", None) or ():
            self._ingest_content(c)

    def _ingest_content(self, c: Any) -> None:
        ctype = _content_type(c)
        if ctype == "text_reasoning" and getattr(c, "text", None):
            self._mark_first_token()
            self.state.reasoning += c.text
            self.state.phase = THINKING
        elif ctype == "text" and getattr(c, "text", None):
            self._mark_first_token()
            self.state.phase = WRITING
            self.state.answer += c.text
        elif ctype == "function_call":
            self._ingest_call(c)
        elif ctype == "function_result":
            cid = getattr(c, "call_id", None) or self._current
            call = self._calls.get(cid) if cid else None
            if call is not None:
                call.done = True
                call.result, call.failed = _result_text(c)
        elif ctype == "usage":
            details = dict(getattr(c, "usage_details", None) or {})
            self._out_sum += details.get("output_token_count") or 0
            self._reason_sum += details.get("reasoning_output_token_count") or 0
            if self._out_sum:
                details["output_token_count"] = self._out_sum
            if self._reason_sum:
                details["reasoning_output_token_count"] = self._reason_sum
            self.state.usage_details = details          # input/total stay last segment's
            self.state.timings = _extract_timings(c)    # last segment's server rate

    def _ingest_call(self, c: Any) -> None:
        name = getattr(c, "name", None)
        cid = getattr(c, "call_id", None)
        if name and cid:
            self._current = cid
            call = ToolCall(call_id=cid, name=name)
            self._calls[cid] = call
            self.state.tool_calls.append(call)
            self.state.phase = SEARCHING
        args = getattr(c, "arguments", None)
        if args and self._current is not None:
            self._calls[self._current].args += args
