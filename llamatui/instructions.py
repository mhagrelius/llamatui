"""System-prompt assembly — where the cache-prefix invariant is enforced by *structure*.

llama-server reuses the longest stable prefix of the prompt across turns. So the one rule that
matters for throughput is: **the only daily-volatile content (the date) must come last**, with
everything stable ahead of it. Previously that rule lived as the *order* of ``append`` calls in
the App plus a comment — easy to break silently.

:func:`build_instructions` makes the rule part of the interface instead. ``volatile`` is a
distinct, always-last slot; a caller cannot put it before the stable parts no matter how it
orders the others. The invariant is therefore testable as a property (see
``tests/test_instructions.py``), not a convention.
"""

from __future__ import annotations

from collections.abc import Iterable


def build_instructions(
    *,
    persona: str,
    capabilities: Iterable[str | None] = (),
    ambient: str | None = None,
    volatile: str | None = None,
) -> str:
    """Compose the system prompt with the volatile slot guaranteed last.

    Order is fixed by structure: ``persona`` → ``capabilities`` (in given order, blanks
    dropped) → ``ambient`` (e.g. the memory preamble) → ``volatile`` (e.g. the date line).
    Everything ahead of ``volatile`` is stable day-to-day, so llama-server's cached prefix
    survives; only the final block changes.
    """
    parts: list[str] = [persona]
    parts.extend(c for c in capabilities if c)
    if ambient:
        parts.append(ambient)
    if volatile:
        parts.append(volatile)  # ALWAYS last — the cache-prefix invariant, enforced here.
    return "\n\n".join(p for p in parts if p)
