"""Graduated, in-memory compaction of the agent-facing Message history.

Deep module: no Textual, no llama-server, no Settings import. Operates on
``list[agent_framework.Message]`` and returns a shorter list. See
docs/superpowers/specs/2026-06-26-context-compaction-design.md.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agent_framework import Content, Message

Summarizer = Callable[[list[Message]], Awaitable[str]]


@dataclass(frozen=True)
class CompactionConfig:
    enabled: bool = True
    trigger: float = 0.60          # start compacting at 60% context (internal default)
    emergency: float = 0.85        # emergency band (internal default)
    keep_recent_turns: int = 5     # the recent window — never compacted (user-facing)
    use_llm_summary: bool = True   # rolling summary via Summarizer; else heuristic (user-facing)
    summary_max_chars: int = 280   # heuristic per-turn budget
    summary_timeout_s: float = 30.0

    @property
    def summarize_threshold(self) -> float:
        return (self.trigger + self.emergency) / 2


@dataclass
class CompactionResult:
    dropped_messages: int = 0
    removed_images: int = 0
    summarized_turns: int = 0

    def changed(self) -> bool:
        return bool(self.dropped_messages or self.removed_images or self.summarized_turns)

    def note(self) -> str:
        parts: list[str] = []
        if self.summarized_turns:
            parts.append(f"summarized {self.summarized_turns} earlier turns")
        if self.removed_images:
            parts.append(f"removed {self.removed_images} images from model context")
        if self.dropped_messages:
            parts.append(f"dropped {self.dropped_messages} messages")
        return ", ".join(parts) if parts else "no change"


_OVERFLOW_KEYWORDS = (
    "context", "exceed", "too long", "token limit",
    "max_tokens", "maximum context", "n_ctx", "overflow",
)


def is_context_overflow(exc: BaseException) -> bool:
    """Heuristic: does this exception look like a context-window overflow?"""
    blobs = [str(exc)]
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        blobs.append(str(cause))
    status = getattr(exc, "status_code", None)
    body = str(getattr(exc, "body", "") or "")
    if status in (400, 413, 422):
        blobs.append(body)
    haystack = " ".join(blobs).lower()
    return any(kw in haystack for kw in _OVERFLOW_KEYWORDS)


def _is_image_content(c: Content) -> bool:
    if getattr(c, "type", None) != "data":
        return False
    return (getattr(c, "media_type", None) or "").startswith("image/")


def _extract_text(msg: Message) -> str:
    for c in msg.contents:
        if getattr(c, "type", None) == "text":
            return getattr(c, "text", "") or ""
    return ""


def _rebuild(msg: Message, *, contents=None, mark: bool = False) -> Message:
    """Copy a Message with optional new contents / compaction marker.

    ``agent_framework.Message`` is not a dataclass/pydantic model and has no
    ``model_copy`` — construct a fresh one, preserving identity fields (this is
    the same fallback ``client.py`` uses)."""
    props = dict(msg.additional_properties or {})
    if mark:
        props["compacted"] = True
    return Message(
        role=msg.role,
        contents=msg.contents if contents is None else contents,
        author_name=getattr(msg, "author_name", None),
        message_id=getattr(msg, "message_id", None),
        additional_properties=props,
    )


def _mark_compacted(msg: Message) -> Message:
    return _rebuild(msg, mark=True)


def _is_compacted(msg: Message) -> bool:
    return bool((msg.additional_properties or {}).get("compacted"))


def _text_msg(role: str, text: str, *, compacted: bool = False) -> Message:
    msg = Message(role=role, contents=[Content.from_text(text=text)])
    return _mark_compacted(msg) if compacted else msg


def overflow_recoverable(*, attempts: int, enabled: bool,
                         approvals_resolved: bool, exc: BaseException) -> bool:
    """Gate for reactive overflow recovery (ADR-0004): recover only on a fresh
    overflow, before any approval-gated action ran, once, and only when enabled."""
    return attempts == 0 and enabled and not approvals_resolved and is_context_overflow(exc)
