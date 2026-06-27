# Overflow recovery stops at the side-effect boundary

When a turn fails with a context-overflow error, [[Compaction]]'s reactive path compacts
history toward the progress floor and retries the turn **once** — but only while *no
approval-gated action has resolved that turn*. Once the user has approved a tool (e.g. a
`filesystem` write), an overflow surfaces as a plain error with no auto-retry, because
re-running the turn from compacted history would re-drive the model into **repeating those
side effects** — and would be futile anyway, since mid-turn overflow is typically caused by a
large tool result living in the per-turn agent session, which compacting `Conversation._messages`
does not remove.

## Considered options

- *Always retry on overflow* (the OMP-inspired default in the handed-off plan). Rejected:
  duplicates side effects of already-approved actions and cannot address session-resident
  tool-result bloat, so it loops back to the same overflow after re-running the tools.
- *Restructure to retry only the failed continuation, preserving prior tool results.* Rejected
  for v1: the bloat is usually the tool result itself, so preserving it doesn't help; real
  complexity for a rare case.

## Consequences

- Recovery covers the common case (the initial prompt overflows before any tool ran) and
  deliberately gives up on mid-turn-after-tools overflow, surfacing an honest error instead.
- This composes with the **off-means-off** stance: with `compaction_enabled=False`, recovery
  is disabled entirely and *any* overflow is a plain error. In both cases the user's escape
  hatch is **manual compaction** (`Ctrl+K` / `/compact`), which is always available.
- `generate()` must track an `approvals_resolved` flag and gate recovery on it; the retry is
  bounded to a single attempt.
- The principled fix for the punted case (mid-turn overflow from a large tool result) is the
  framework's native in-place `CompactionStrategy` on `agent.run`, which can shrink the
  session-resident result without re-running the tool. Logged as a future item in the design
  spec, not in v1 scope.
