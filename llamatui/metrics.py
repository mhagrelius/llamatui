"""Throughput / token metrics extracted from a streamed turn.

Two sources feed this:

* MAF's ``UsageContent.usage_details`` -> token counts.
* llama-server's non-standard ``timings`` block (left on the raw chunk by the client)
  -> real prefill (prompt) and generation (predicted) tokens/sec plus speculative-decode
  draft acceptance. When ``timings`` is absent we fall back to client-side wall-clock rates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnMetrics:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None

    prefill_tok_s: float | None = None      # prompt processing speed
    gen_tok_s: float | None = None          # token generation speed
    prompt_processed: int | None = None     # prompt tokens actually evaluated (uncached)
    cached_tokens: int | None = None        # prompt tokens served from cache

    ttft_s: float | None = None             # time to first token (any kind)
    elapsed_s: float | None = None          # wall-clock for the whole turn

    draft_n: int | None = None              # speculative decoding: drafted tokens
    draft_accepted: int | None = None       # ... of which accepted

    context_used: int | None = None
    context_window: int | None = None

    @property
    def accept_rate(self) -> float | None:
        if self.draft_n:
            return (self.draft_accepted or 0) / self.draft_n
        return None

    @property
    def context_frac(self) -> float | None:
        if self.context_used is not None and self.context_window:
            return min(1.0, self.context_used / self.context_window)
        return None


def extract(
    usage_details: dict[str, Any] | None,
    timings: dict[str, Any] | None,
    *,
    ttft_s: float | None,
    elapsed_s: float | None,
    answer_chars: int = 0,
    context_window: int | None = None,
) -> TurnMetrics:
    """Fold usage + timings + client timers into a single TurnMetrics."""
    m = TurnMetrics(ttft_s=ttft_s, elapsed_s=elapsed_s, context_window=context_window)

    if usage_details:
        m.input_tokens = usage_details.get("input_token_count")
        m.output_tokens = usage_details.get("output_token_count")
        m.total_tokens = usage_details.get("total_token_count")
        m.cached_tokens = usage_details.get("cache_read_input_token_count")
        m.reasoning_tokens = usage_details.get("reasoning_output_token_count") or usage_details.get(
            "completion/reasoning_tokens"
        )

    if timings:
        m.prefill_tok_s = timings.get("prompt_per_second")
        m.gen_tok_s = timings.get("predicted_per_second")
        m.prompt_processed = timings.get("prompt_n")
        m.draft_n = timings.get("draft_n")
        m.draft_accepted = timings.get("draft_n_accepted")
        if m.input_tokens is None:
            m.input_tokens = timings.get("prompt_n")
        if m.output_tokens is None:
            m.output_tokens = timings.get("predicted_n")

    # "Context used" = the tokens the model actually had in context during the turn
    # (full prompt + generation). This is the meaningful "how close to the window" number;
    # the bare prompt size understates it because reasoning is dropped between turns, and
    # collapses toward 1 on cache-heavy turns.
    if m.total_tokens is not None:
        m.context_used = m.total_tokens
    elif m.input_tokens is not None or m.output_tokens is not None:
        m.context_used = (m.input_tokens or 0) + (m.output_tokens or 0)

    # Fallback generation rate from wall-clock when the server didn't report timings.
    if m.gen_tok_s is None and m.output_tokens and elapsed_s and ttft_s is not None:
        gen_time = max(1e-6, elapsed_s - ttft_s)
        m.gen_tok_s = m.output_tokens / gen_time

    return m


def _fmt_int(n: int | None) -> str:
    return f"{n:,}" if isinstance(n, int) else "–"


def format_oneline(m: TurnMetrics) -> str:
    """A compact single-line summary for the per-turn footer of a message."""
    parts: list[str] = []
    if m.output_tokens is not None:
        parts.append(f"{_fmt_int(m.output_tokens)} out")
    if m.input_tokens is not None:
        parts.append(f"{_fmt_int(m.input_tokens)} in")
    if m.reasoning_tokens:
        parts.append(f"{_fmt_int(m.reasoning_tokens)} think")
    if m.gen_tok_s is not None:
        parts.append(f"{m.gen_tok_s:.1f} tok/s")
    # Prefill speed is only meaningful over a non-trivial prefill; for a ~20-token prompt
    # it's dominated by fixed launch overhead, so we omit it rather than show a misleading
    # low number. Substantial prompts (pasted code, long context) still surface it.
    if m.prefill_tok_s is not None and (m.prompt_processed or 0) >= 32:
        parts.append(f"pp {m.prefill_tok_s:.0f}")
    if m.ttft_s is not None:
        parts.append(f"ttft {m.ttft_s*1000:.0f}ms")
    if m.elapsed_s is not None:
        parts.append(f"{m.elapsed_s:.1f}s")
    if m.accept_rate is not None:
        parts.append(f"draft {m.accept_rate*100:.0f}%")
    return "  ·  ".join(parts)
