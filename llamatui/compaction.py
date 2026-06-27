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


def _strip_images_from(msg: Message) -> tuple[Message, int]:
    """Return (message with image parts replaced by an '[image removed]' text part, count removed).

    The original text stays the FIRST text part so _extract_text still surfaces the real text;
    the placeholder is appended as a separate part. Returns (msg, 0) unchanged when no images."""
    images = [c for c in msg.contents if _is_image_content(c)]
    if not images:
        return msg, 0
    kept = [c for c in msg.contents if not _is_image_content(c)]
    kept.append(Content.from_text(text="[image removed]"))
    return _rebuild(msg, contents=kept, mark=True), len(images)


def overflow_recoverable(*, attempts: int, enabled: bool,
                         approvals_resolved: bool, exc: BaseException) -> bool:
    """Gate for reactive overflow recovery (ADR-0004): recover only on a fresh
    overflow, before any approval-gated action ran, once, and only when enabled."""
    return attempts == 0 and enabled and not approvals_resolved and is_context_overflow(exc)


class Compactor:
    """Graduated compaction over a Message list. Framework-free; the only
    non-pure dependency is the injected async ``summarizer`` seam."""

    def __init__(self, summarizer: Summarizer | None = None) -> None:
        self._summarizer = summarizer

    def should_compact(self, context_frac: float, cfg: CompactionConfig) -> bool:
        return context_frac >= cfg.trigger

    @staticmethod
    def _recent_cut(messages: list[Message], keep: int) -> int:
        return max(0, len(messages) - 2 * keep)

    def _strip_old_images(
        self, messages: list[Message], cfg: CompactionConfig
    ) -> tuple[list[Message], int]:
        cut = self._recent_cut(messages, cfg.keep_recent_turns)
        if cut <= 1:
            return messages, 0
        removed = 0
        out = list(messages)
        for i in range(1, cut):                 # skip index 0 (first user msg)
            if _is_compacted(out[i]):
                continue
            out[i], n = _strip_images_from(out[i])
            removed += n
        return out, removed

    def _aged_region(self, messages, cut):
        """Return (start, end, existing_summary_text) for the foldable region [start, end)."""
        if cut <= 1:
            return 1, 1, ""
        start, existing = 1, ""
        if len(messages) > 1 and _is_compacted(messages[1]) and messages[1].role == "assistant":
            existing = _extract_text(messages[1])
            start = 2
        return start, cut, existing

    def _heuristic_summary(self, existing: str, region: list, cfg: CompactionConfig) -> tuple:
        lines = [existing] if existing else []
        turns = 0
        i = 0
        if region and region[0].role == "assistant":
            # leading orphan answer (e.g. the first turn's reply, whose user is
            # preserved separately at index 0) — keep it instead of dropping it.
            lines.append(f"- (earlier reply): {_extract_text(region[0])[:cfg.summary_max_chars]}")
            i = 1
        while i < len(region):
            msg = region[i]
            if msg.role != "user":
                i += 1
                continue
            user_line = _extract_text(msg).splitlines()[0] if _extract_text(msg) else ""
            answer = ""
            if i + 1 < len(region) and region[i + 1].role == "assistant":
                answer = _extract_text(region[i + 1])
                i += 2
            else:
                i += 1
            lines.append(f"- {user_line[:80]}: {answer[:cfg.summary_max_chars]}")
            turns += 1
        return "\n".join(lines), turns

    async def _fold_rolling_summary(self, messages, cfg):
        cut = self._recent_cut(messages, cfg.keep_recent_turns)
        start, end, existing = self._aged_region(messages, cut)
        region = messages[start:end]
        if not region:
            return messages, 0
        if cfg.use_llm_summary and self._summarizer is not None:
            text, turns = await self._llm_summary(existing, region, cfg)
        else:
            text, turns = self._heuristic_summary(existing, region, cfg)
        if turns == 0:
            return messages, 0
        summary = _text_msg("assistant", text, compacted=True)
        out = messages[:1] + [summary] + messages[end:]
        return out, turns

    async def _llm_summary(self, existing, region, cfg):
        return self._heuristic_summary(existing, region, cfg)

    async def compact(
        self, messages: list[Message], context_frac: float, cfg: CompactionConfig
    ) -> tuple[list[Message], CompactionResult]:
        result = CompactionResult()
        before = len(messages)
        messages, removed = self._strip_old_images(messages, cfg)
        result.removed_images += removed
        if context_frac >= cfg.summarize_threshold:
            messages, turns = await self._fold_rolling_summary(messages, cfg)
            result.summarized_turns += turns
        result.dropped_messages += before - len(messages)
        return messages, result
