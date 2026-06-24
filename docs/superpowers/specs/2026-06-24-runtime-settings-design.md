# Runtime settings for llamatui — design

**Status:** approved (design), pending implementation plan
**Date:** 2026-06-24

## Goal

A runtime **settings** concept: open a modal panel, change values, and have them take
effect immediately and persist for next launch — without restarting or editing CLI flags.
v1 covers the sampling knobs (thinking budget, temperature, top-p, max tokens), the voice
input *mode* (toggle vs hold-to-speak), and thinking-pane visibility.

## Decisions (locked)

| Question | Decision |
|---|---|
| Surface | A **modal settings panel** (Textual `ModalScreen`), opened by **`Ctrl+,`** (fall back to the reliable `Ctrl+T`, freed below, if a terminal doesn't deliver `Ctrl+,`). No slash-command surface — the user doesn't use them. |
| Thinking visibility | Panel-only. **`Ctrl+T` and `/think` are removed**; `show_thinking` is set exclusively through the panel and persists (so the visibility preference now sticks across launches, instead of resetting to shown each launch). |
| Persistence | **Persist to a JSON file** under the per-user data dir; becomes the defaults next launch. |
| Precedence | **CLI flag > saved file > built-in default.** A one-off CLI flag wins for that run and does **not** overwrite the file. |
| v1 settings | Sampling knobs (`thinking_budget`, `temperature`, `top_p`, `max_tokens`), `voice_mode` (toggle/hold), `show_thinking`. |
| Save semantics | **Load never writes the file.** The panel shows effective values; **Save merges only the fields the user changed** onto the existing file, so a one-off CLI flag never leaks into persistence. |
| Cache safety | Sampling changes rebuild the agent from **cached instructions** — never recompute the system prompt mid-conversation, so the KV-cache prefix survives. |
| Hold-to-talk mechanism | **Auto-repeat gap heuristic** (terminals don't give key-release; Textual doesn't enable Kitty event-type reporting — see "Why not real key-release"). Toggle stays the default. |

## Non-goals (YAGNI)

- **No runtime feature on/off** for web / memory / voice in v1 — those own resources
  (MCP connection, whisper subprocess, embedder) whose connect/disconnect lifecycle is
  out of scope. The `Settings` schema is built to accept these fields later without
  restructuring.
- **No per-field edit tracking.** Save persists the full effective set (see Save
  semantics). The "CLI wins, file untouched" guarantee holds for any run where the panel
  is not explicitly Saved.
- **No true push-to-talk** (release-based). Blocked upstream (below); the heuristic is the
  portable substitute.
- **No live-reload of a hand-edited settings file**, no settings profiles, no import/export,
  no settings search. One flat panel.
- **No model/url/db/whisper-path editing from the panel** — those remain bootstrap `Config`,
  set via CLI.

## Why not real key-release (research result)

Textual 8.2.7 uses the Kitty keyboard protocol **only for escape-code disambiguation**, not
event reporting:

- Windows driver pushes `\x1b[>1u` — flag `1` only (`windows_driver.py`).
- Linux driver pushes `1 | 8 | 16 = 25` — disambiguate + report-all-keys + associated-text,
  but **not** flag `2` ("report event types"), which the Kitty protocol *requires* for
  press/repeat/release (`linux_driver.py`).
- The Kitty sequence parser never decodes the `event_type` subfield (`_xterm_parser.py`),
  and `events.Key` has no press/release attribute (`events.py`).

So no Textual app at this version can observe key-up, even on a Kitty-capable terminal.
Holding `Ctrl+R` instead produces an auto-repeat **burst** of key-down events under every
mode, which the heuristic below interprets. (Sources: Kitty keyboard-protocol spec; Blessed
docs on the report-events flag.) The full rationale — and why we rejected real key-release and
a single fixed timeout — lives in **`docs/adr/0002-hold-to-talk-via-auto-repeat-gap.md`**.

## Architecture

The codebase rule (`CONTEXT.md`): one module = one concern, narrow interface, `app.py` stays
a thin adapter. This feature adds **one deep module** (`settings.py`) and **one modal screen**
(`settings_screen.py`), widens two existing interfaces (`Dictation`, `paths`), and splits one
app method. Each module's interface is its test surface.

### Module: `settings.py` → `Settings`

Owns *the runtime-mutable, persisted preferences* and their precedence + persistence. The
single source of truth for built-in defaults. Knows nothing about Textual, the agent, or the
keyboard.

```
class VoiceMode(str, Enum):
    TOGGLE = "toggle"
    HOLD   = "hold"

@dataclass
class Settings:
    thinking_budget: int          # N>0 budget · 0 off · -1 unlimited
    temperature: float
    top_p: float | None
    max_tokens: int
    voice_mode: VoiceMode
    show_thinking: bool
```

Interface:

- `DEFAULTS: Settings` — the built-in defaults (`thinking_budget=8192`, `temperature=0.7`,
  `top_p=None`, `max_tokens=32000`, `voice_mode=TOGGLE`, `show_thinking=True`). The CLI help
  text advertises these.
- `load(path: Path, cli: dict) -> Settings` — **precedence resolution**: start from
  `DEFAULTS`, overlay any values present in the saved file at `path`, then overlay any
  **non-`None`** entries in `cli`. Pure; **never writes**. Forgiving: a missing/malformed
  file or unknown keys degrade to defaults, never raise.
- `save_changes(path: Path, changed: dict) -> None` — **field-level merge**: read the
  existing file (forgiving), overlay only the keys in `changed`, re-stamp `"version": 1`, and
  write back as JSON. Creates the parent dir if needed. Persisting only what the user changed
  in the panel keeps a one-off CLI flag from leaking into the file (see Save semantics).
- `to_dict()` / `from_dict(d)` — round-trip helpers; `from_dict` ignores unknown keys, fills
  missing ones from `DEFAULTS`, and parses `voice_mode` forgivingly (unknown → `TOGGLE`).

### Module: `settings_screen.py` → `SettingsScreen`

A `ModalScreen[Settings | None]` rendering the current `Settings` as labelled controls:

| Field | Control | Hint / validation |
|---|---|---|
| `thinking_budget` | integer `Input` | `N>0 budget · 0 off · -1 unlimited`; integer ≥ -1 |
| `temperature` | float `Input` | `0.0–2.0` |
| `top_p` | float `Input` | `0.0–1.0`; **blank = off** (`None`) |
| `max_tokens` | integer `Input` | integer > 0 |
| `voice_mode` | Toggle/Hold selector (`RadioSet` or `Select`) | — |
| `show_thinking` | `Switch` | — |

- **Enter = Save, Esc = Cancel.** Invalid numerics block Save with an inline message; the
  screen does not dismiss until inputs validate.
- On Save, `dismiss(new_settings)`; on Cancel, `dismiss(None)`.
- The screen is **construction-pure**: it takes the current `Settings` in and returns a new
  `Settings` out. It does not touch the agent, the file, or the app — the app applies the
  result. (Keeps the validation/build logic testable without app wiring.)

### `paths.py` — addition only

- `settings_path() -> Path` = `user_data_dir() / "settings.json"`. Same root as the
  conversations DB and whisper assets, so it is found regardless of cwd.

### `dictation.py` → `Dictation` — interface widening only

Hold mode needs explicit start/stop rather than toggle semantics. Add two public verbs
alongside the existing `toggle()`; the state machine and its seams are otherwise unchanged.

- `start()` — `idle → recording` (same body as today's `_start`); **no-op** if already
  recording or transcribing.
- `stop()` — `recording → transcribing` (same body as today's `_stop`, including the
  min-duration guard); **no-op** if idle or transcribing.
- `toggle()` — unchanged behaviour, now expressed in terms of `start()`/`stop()`.
- `cancel()` — **quiet stop that discards**: `recording → idle` closing the mic stream
  *without* transcribing; **no-op** if idle or transcribing. Used when `voice_mode` changes
  mid-recording, so the next dictation starts cleanly under the new key→verb mapping.

### `Config` (`app.py`) — slimmed

The four sampling knobs **move out of `Config` into `Settings`**. `Config` keeps only
bootstrap: `url`, `model`, `system`, `db_path`, `web`, `memory`, `voice`, `whisper_bin`,
`whisper_model`, `whisper_url`. The app holds one `self.settings: Settings`.

### `app.py` (thin adapter) — the agent-rebuild split + wiring

`_rebuild_agent` is split so sampling changes never disturb the cache prefix:

- `_build_instructions() -> str` — the existing semi-volatile composition (persona →
  capabilities → ambient memory → volatile date). Called **only at conversation boundaries**
  (mount, `/system`, new chat, open conversation). Result cached in `self._instructions`.
- `_apply_agent() -> None` — `build_agent(...)` from the **cached** `self._instructions` plus
  `self.settings` sampling. Called at boundaries (after `_build_instructions`) **and** after a
  panel edit that changed any sampling field.

`_rebuild_agent()` becomes `self._instructions = self._build_instructions(); self._apply_agent()`.

**Mid-stream safety.** The panel can be opened (and saved) while a turn is generating — the
opener is not gated on `_busy`. Sampling changes apply to the **next** turn: the `generate`
worker reads `self.agent` once to build the streaming iterator, so `_apply_agent` swapping in a
new agent leaves the in-flight iterator bound to the old agent object (still alive, no shared
mutable state). A `show_thinking` change re-runs pane visibility across all turns including the
live one, which is harmless.

Settings lifecycle in the app:

- `on_mount`: `self.settings = settings.load(paths.settings_path(), cli_overrides)`.
- New `action_open_settings()` (bound to **`Ctrl+,`**) does
  `push_screen(SettingsScreen(self.settings), self._on_settings_closed)`. No `/settings`
  command. The `Ctrl+T` binding, the `action_toggle_thinking` method, and the `/think` command
  are **removed**; the freed `Ctrl+T` is the fallback opener key if `Ctrl+,` proves unreliable
  on a given terminal.
- `_on_settings_closed(result)`: if `None`, do nothing. Else compute the **changed set**
  (`{field: new[field]}` for fields where `old != new`) and act on it:
  - any sampling field changed → `self.settings = result; self._apply_agent()`.
  - `show_thinking` changed → update `self.settings` and re-run pane visibility across
    existing `AssistantTurn`s (the pane-visibility loop lifted out of the now-removed
    `action_toggle_thinking`).
  - `voice_mode` changed → store it (read live by `action_dictate`) **and** if a recording is
    in flight, `dictation.cancel()` it to idle, so the next dictation starts under the new
    mapping (avoids a toggle-started recording stranded with no hold poll timer).
  - if the changed set is non-empty → `Settings.save_changes(paths.settings_path(), changed)`
    (merge only those fields; untouched CLI overrides never get written).
- `show_thinking` reads move from `self.show_thinking` to `self.settings.show_thinking`
  (e.g. the `AssistantTurn(show_thinking=...)` construction at mount/open). It is written only
  via the panel; there is no in-session toggle key, so memory and disk never drift and the
  merge-on-save diff stays uniform across all fields.

### Voice mode dispatch (`action_dictate`)

Branch on `self.settings.voice_mode`:

- **TOGGLE** — unchanged: `_Debouncer` collapses held auto-repeat, then `dictation.toggle()`,
  arm/disarm the existing 120 s cap timer.
- **HOLD** — a small framework-free helper (mirroring `_Debouncer`) interprets the key
  stream with a **two-phase release gap**, because terminals (and Textual) expose no
  key-release; "release" is inferred from a gap in the OS **auto-repeat** burst.
  - first `Ctrl+R` while idle → `dictation.start()`, arm the 120 s cap timer, **and** arm a
    repeating poll timer (~0.05 s). Mark the recording as *not yet repeating*.
  - each subsequent `Ctrl+R` → push `last_key = now`; the **second** keydown flips the
    recording to *repeating* (auto-repeat confirmed live).
  - the poll timer fires `dictation.stop()` and disarms itself once `now - last_key` exceeds
    the active gap:
    - **before the first repeat** (still inside the initial-delay pause): gap =
      `D + INITIAL_MARGIN` (~`D + 0.30 s`). We must wait at least this long, since a genuine
      hold is silent until the first repeat at ~`D`.
    - **after a repeat is seen** (gaps are now ~33 ms): gap = `RELEASE_GAP_ACTIVE` (~0.20 s),
      so a real hold-to-talk stops crisply ~0.2 s after release, **independent of `D`**.
  - the 120 s cap stays as a safety stop.

`D` (the OS initial key-repeat delay) is read once at startup via
`SystemParametersInfo(SPI_GETKEYBOARDDELAY)` (0–3 → ~250/500/750/1000 ms), in a small
Windows-only `ctypes` helper that returns a safe default (~0.5 s) if the call fails. The helper
feeds `D` into the hold controller's constructor; the controller itself stays pure and
framework-free (injected `now`, injected gap params), so it is unit-tested with a fake clock.

**No auto-fallback to toggle.** A sub-`D` tap is indistinguishable from a no-auto-repeat
terminal (both are a single keydown then silence), so per-recording detection would misfire on
ordinary short holds. Standard Windows terminals deliver OS key auto-repeat as repeated key
events, so hold works there; the requirement is documented, and toggle stays the reliable
default. On a genuinely non-repeating terminal, each hold degrades to one ~`(D + margin)` clip
— usable, not a hang.

The Dictation state machine is untouched by mode; only the app's key→verb mapping differs.

## Data flow

### Editing a setting

```
Ctrl+,  → action_open_settings()
  → push_screen(SettingsScreen(self.settings))
       user edits controls; Enter validates → dismiss(new_settings)
  → _on_settings_closed(new_settings)
       changed = {field: new[field] for field where old != new}
       sampling changed?      → self.settings = new; _apply_agent()   # cached instructions → prefix intact
       show_thinking changed? → update panes across AssistantTurns
       voice_mode changed?    → store (read live by action_dictate)
       changed non-empty?     → save_changes(settings_path(), changed)  # merge only changed fields
```

### Hold-mode dictation

```
Ctrl+R (held) → action_dictate()  [voice_mode == HOLD]   # D = SPI_GETKEYBOARDDELAY, read at startup
  first event (idle):  dictation.start(); arm 120s cap; arm poll timer (~0.05s); repeating=False
  2nd event:           last_key = now; repeating = True       # auto-repeat confirmed live
  later repeats:       last_key = now                         # deadline pushed forward
  poll timer:          gap = (D + 0.30s) if not repeating else 0.20s
                       now - last_key > gap →  dictation.stop(); disarm poll timer
                       (transcribe runs via the existing background worker)
```

## Precedence & CLI changes (`__main__.py`)

The settings-managed flags switch to `default=None` **sentinels** so "passed" is
distinguishable from "defaulted"; the help text quotes `settings.DEFAULTS`. A new
`--voice-mode {toggle,hold}` flag is added. The parsed values become the `cli` dict passed to
`settings.load`.

| Arg | Settings field | Sentinel default | Effective default (from `DEFAULTS`) |
|---|---|---|---|
| `--thinking-budget N` | `thinking_budget` | `None` | `8192` |
| `--temp F` | `temperature` | `None` | `0.7` |
| `--top-p F` | `top_p` | `None` | `None` (off) |
| `--max-tokens N` | `max_tokens` | `None` | `32000` |
| `--voice-mode {toggle,hold}` | `voice_mode` | `None` | `toggle` |

`show_thinking` has no CLI flag (it is panel-only); it loads from file or defaults to `True`. The bootstrap flags (`--url`, `--model`, `--system`, `--db`, `--no-web`,
`--no-memory`, `--no-voice`, `--whisper-*`) are unchanged and stay on `Config`.

## Error handling

| Failure | Behavior |
|---|---|
| settings file missing | use `DEFAULTS` (overlaid by any CLI flags); first Save creates it |
| settings file malformed / not JSON | treat as empty → `DEFAULTS`; never crash; first Save overwrites it |
| unknown / extra keys in file | ignored on load |
| missing keys in file | filled from `DEFAULTS` |
| invalid `voice_mode` string | parsed to `TOGGLE` |
| invalid numeric typed in panel | inline error; Save blocked; screen stays open |
| Save can't write (I/O error) | surface a one-line status note; in-memory settings still apply for the session |
| hold mode on a terminal with no key auto-repeat | every hold is one keydown → one ~`(D + margin)` clip stopped by the before-first-repeat gap (degraded but usable; not a hang). Toggle remains the reliable default |
| `SPI_GETKEYBOARDDELAY` query fails | hold controller uses a safe default `D` (~0.5 s); no crash |

## Testing

Interface = test surface. No Textual required for the core logic.

- **`settings.py`** (`tests/test_settings.py`):
  - precedence: `DEFAULTS` < file < CLI; non-`None` CLI overrides win; `None` CLI entries
    don't clobber file values.
  - `save_changes` round-trip: merging a subset onto an existing file overlays only those
    keys and leaves the rest (including a value that differs from `DEFAULTS`) untouched;
    merging onto a missing file creates it; round-trips `top_p=None` and each `voice_mode`.
  - malformed/empty/missing file → `DEFAULTS`, no raise.
  - unknown keys ignored; missing keys filled; bad `voice_mode` → `TOGGLE`.
  - `load` does **not** write the file.
- **Hold controller** (`tests/test_dictation.py`): a pure helper driven by a fake clock and
  injected `D` —
  - first key starts; **before any repeat**, stops only after `D + INITIAL_MARGIN`.
  - a second key flips to *repeating*; thereafter stops ~`RELEASE_GAP_ACTIVE` after the last
    key — verify a long stream of repeats keeps it alive and a real release stops it crisply,
    **independent of `D`**.
  - a single keydown then silence (no-repeat terminal / sub-`D` tap) yields one
    `D + INITIAL_MARGIN` clip, no hang.
  - no-op after stop. (The `SPI_GETKEYBOARDDELAY` call is **not** exercised in tests; `D` is
    injected — the OS helper is trivial and Windows-only.)
- **`Dictation`** (`tests/test_dictation.py`): new `start()`/`stop()`/`cancel()` —
  `start` while recording = no-op; `stop` while idle = no-op; `cancel` while recording → idle
  **without** invoking the transcriber, and is a no-op while idle/transcribing; `toggle()`
  still drives idle→recording→transcribing→idle.
- **`SettingsScreen`**: validation/build logic tested through its pure in→out contract
  (current `Settings` → edited `Settings`); a light `run_test` pilot only if cheap.

## Files touched / added

- **add** `llamatui/settings.py` — `Settings`, `VoiceMode`, `DEFAULTS`, `load`, `save_changes`
- **add** `llamatui/settings_screen.py` — `SettingsScreen` modal
- **add** `tests/test_settings.py` — precedence, persistence, hold controller
- **add** `docs/adr/0002-hold-to-talk-via-auto-repeat-gap.md` (done)
- **edit** `llamatui/paths.py` — `settings_path()`
- **edit** `llamatui/dictation.py` — public `start()` / `stop()` / `cancel()` verbs
- **edit** `tests/test_dictation.py` — `start`/`stop` idempotency
- **edit** `llamatui/app.py` — `Config` slimmed; `self.settings`; `_build_instructions` /
  `_apply_agent` split; `action_open_settings` (`Ctrl+,`) + `_on_settings_closed`; **remove**
  `Ctrl+T` binding, `action_toggle_thinking`, and the `/think` command (+ its `HELP` line);
  `voice_mode` dispatch + two-phase hold controller (pure class beside `_Debouncer`) + the
  `SPI_GETKEYBOARDDELAY` `ctypes` helper; `show_thinking` moves into settings, panel-only
- **edit** `llamatui/__main__.py` — sentinel defaults; `--voice-mode`; build `cli` dict;
  `settings.load`
- **edit** `CONTEXT.md` — `Settings` (and the `_build_instructions`/`_apply_agent` seam)
  glossary entry
- **edit** `README.md` — settings panel + `Ctrl+,`; note `Ctrl+T` / `/think` removed; `--voice-mode`
