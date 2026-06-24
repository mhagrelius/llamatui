# Hold-to-talk is inferred from the OS key auto-repeat burst, not key-release

**Status:** accepted

The voice dictation **hold** mode (hold `Ctrl+R` to record, release to stop) is implemented by
watching the key's **OS auto-repeat burst** and inferring "released" from a gap in that burst —
*not* from a key-release event, because none is available. The initial key-repeat delay `D` is
read once at startup via `SystemParametersInfo(SPI_GETKEYBOARDDELAY)` and feeds a **two-phase
release gap**: `D + margin` before the first repeat is seen, then a short (~0.2 s) gap once
auto-repeat is confirmed live. We chose this over the two obvious alternatives — real
key-release via the Kitty keyboard protocol, and a single fixed timeout constant — for specific
reasons.

## Why not real key-release (the Kitty keyboard protocol)

Terminals don't emit key-release by default; the Kitty keyboard protocol can, but only when the
application enables its **"report event types"** progressive-enhancement flag (bit `2`).
**Textual (8.2.7) does not enable it.** Its drivers push flag `1` only on Windows
(`\x1b[>1u`) and `1|8|16` on Linux — disambiguation, report-all-keys, associated-text, but
never `2`. The Kitty sequence parser doesn't decode the `event_type` subfield, and `events.Key`
has no press/release attribute. So **no Textual app at this version can observe key-up**, even
on a fully Kitty-capable terminal (WezTerm, Kitty, foot). Getting real release events would mean
patching Textual's driver *and* parser *and* event model — fragile across upgrades, and Windows
Terminal's Kitty support is partial regardless. Not worth it for one interaction mode.

Because the protocol isn't enabled, holding a key instead produces a **burst of repeated
key-down events** (OS auto-repeat) under every mode — which is the signal we key off.

## Why two phases, not a single fixed timeout

The keep-alive gap must exceed `D` (the initial repeat delay), or a genuine hold false-stops
during the silent pause before auto-repeat kicks in. But `D` is **user-configurable** on Windows
(~250 ms–1000 ms), so no single constant is safe: too small breaks slow-repeat machines; large
enough to be safe (~1.1 s) makes every release feel sluggish. Reading the real `D` and using it
only for the *before-first-repeat* phase — then shrinking the gap to ~0.2 s once repeat is
confirmed — makes a real hold stop crisply **independent of `D`**, while still tolerating the
initial pause. The big-`D` latency then applies only to sub-`D` taps, where waiting is
unavoidable anyway.

## Why no auto-fallback to toggle

A sub-`D` tap (one keydown, then silence) is **indistinguishable** from a terminal that can't
auto-repeat at all. So per-recording "detect no-repeat and switch to toggle" would misfire on
ordinary short holds. Since this app is Windows-only and standard Windows terminals deliver OS
auto-repeat, hold works in practice; the requirement is documented and **toggle stays the
reliable default**. On a genuinely non-repeating terminal, each hold degrades to one
`D + margin` clip — usable, not a hang.

## Consequences

- A small Windows-only `ctypes` helper queries `SPI_GETKEYBOARDDELAY` at startup, with a safe
  default (~0.5 s) if the call fails. The hold controller itself stays pure and framework-free
  (injected `now`, injected `D`), so it is unit-tested with a fake clock.
- Hold-to-talk has a small inherent stop latency (~0.2 s after release for a real hold) and is
  best-effort on terminals without key auto-repeat. **Toggle** remains the default and the
  always-reliable path.
- If a future Textual enables the Kitty event-types flag (or exposes key-release), this whole
  mechanism can be replaced by a true press/release pair — the `voice_mode` seam and the
  `Dictation.start()/stop()` verbs already isolate the change to the app's key→verb mapping.
