# Runtime Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A modal settings panel (`Ctrl+,`) to change runtime preferences — sampling knobs, voice input mode, thinking-pane visibility — that take effect immediately and persist across launches.

**Architecture:** A new pure `settings.py` module owns the preferences, their precedence (CLI > saved file > default), and field-level persistence. The four sampling knobs move out of `Config` into `Settings`. `app.py`'s agent build splits into `_build_instructions` (cached, conversation-boundary only) + `_apply_agent` (re-runnable from cached instructions) so sampling changes never disturb the KV-cache prefix. A Textual `SettingsScreen` edits a `Settings` in and returns one out; all validation lives in a pure `parse_form`. Voice "hold" mode is driven by a pure two-phase `_HoldController` reading the OS key-repeat delay.

**Tech Stack:** Python 3.11+, Textual ≥0.86, pytest. Standard library only for the new logic (`json`, `dataclasses`, `enum`, `ctypes`) — no new dependencies.

## Global Constraints

- **Platform: Windows-only.** The `SPI_GETKEYBOARDDELAY` query uses `ctypes.windll`; guard it and fall back to a default on any error.
- **No new dependencies.** Use only stdlib + existing deps.
- **Testing style (house rule):** the module interface is the test surface. Pure logic (precedence, persistence, validation, the hold controller, the dictation verbs) is unit-tested with injected fakes/clocks — no running `App`, no real audio, no network. App wiring is thin glue verified by the suite staying green + a manual smoke run.
- **Cache-prefix discipline:** never recompute the system prompt to apply a sampling change. Sampling rides in request options, not the prompt.
- **Precedence is `CLI flag > saved file > built-in default`.** `DEFAULTS` is the single source of the defaults. Loading never writes the file.
- **No new slash commands.** The user does not use them (`/think` is being removed, not relocated).
- Run tests with `uv run pytest`.

---

### Task 1: `Settings` model, precedence, persistence, diff

**Files:**
- Create: `llamatui/settings.py`
- Modify: `llamatui/paths.py` (add `settings_path`)
- Test: `tests/test_settings.py`, `tests/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class VoiceMode(str, Enum)` with `TOGGLE = "toggle"`, `HOLD = "hold"`, and classmethod `parse(value) -> VoiceMode` (forgiving; bad input → `TOGGLE`).
  - `@dataclass(frozen=True) class Settings` with fields `thinking_budget:int=8192`, `temperature:float=0.7`, `top_p:float|None=None`, `max_tokens:int=32000`, `voice_mode:VoiceMode=VoiceMode.TOGGLE`, `show_thinking:bool=True`; method `to_dict() -> dict`.
  - `DEFAULTS = Settings()`
  - `SAMPLING_FIELDS = frozenset({"thinking_budget","temperature","top_p","max_tokens"})`
  - `from_dict(d: dict) -> Settings`
  - `load(path: Path, cli: dict | None = None) -> Settings`
  - `save_changes(path: Path, changed: dict) -> None`
  - `changed_fields(old: Settings, new: Settings) -> dict`
  - `paths.settings_path() -> Path`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_settings.py`:

```python
"""Settings is pure: its interface (precedence, persistence, diff) is the test surface.
No Textual, no agent, no App — same philosophy as test_dictation / test_graph."""

import json
from pathlib import Path

from llamatui.settings import (
    DEFAULTS, SAMPLING_FIELDS, Settings, VoiceMode,
    changed_fields, from_dict, load, save_changes,
)


def test_defaults_match_legacy_cli_defaults():
    assert DEFAULTS.thinking_budget == 8192
    assert DEFAULTS.temperature == 0.7
    assert DEFAULTS.top_p is None
    assert DEFAULTS.max_tokens == 32000
    assert DEFAULTS.voice_mode is VoiceMode.TOGGLE
    assert DEFAULTS.show_thinking is True


def test_voicemode_parse_is_forgiving():
    assert VoiceMode.parse("hold") is VoiceMode.HOLD
    assert VoiceMode.parse("TOGGLE") is VoiceMode.TOGGLE
    assert VoiceMode.parse("garbage") is VoiceMode.TOGGLE
    assert VoiceMode.parse(None) is VoiceMode.TOGGLE
    assert VoiceMode.parse(VoiceMode.HOLD) is VoiceMode.HOLD


def test_load_missing_file_is_defaults(tmp_path: Path):
    assert load(tmp_path / "nope.json") == DEFAULTS


def test_load_malformed_file_is_defaults(tmp_path: Path):
    p = tmp_path / "settings.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load(p) == DEFAULTS


def test_load_file_overrides_defaults(tmp_path: Path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"temperature": 0.2, "voice_mode": "hold"}), encoding="utf-8")
    s = load(p)
    assert s.temperature == 0.2
    assert s.voice_mode is VoiceMode.HOLD
    assert s.max_tokens == DEFAULTS.max_tokens          # untouched key falls to default


def test_load_unknown_keys_ignored_and_missing_filled(tmp_path: Path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"temperature": 0.3, "bogus": 1}), encoding="utf-8")
    s = load(p)
    assert s.temperature == 0.3
    assert s.show_thinking is True


def test_cli_overrides_win_and_none_does_not_clobber(tmp_path: Path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"temperature": 0.2, "max_tokens": 1000}), encoding="utf-8")
    # CLI passes temperature=0.9, leaves max_tokens unset (None sentinel)
    s = load(p, {"temperature": 0.9, "max_tokens": None})
    assert s.temperature == 0.9          # CLI wins
    assert s.max_tokens == 1000          # None sentinel did not clobber the file value


def test_load_never_writes_file(tmp_path: Path):
    p = tmp_path / "settings.json"
    load(p, {"temperature": 0.9})
    assert not p.exists()


def test_save_changes_merges_only_given_fields(tmp_path: Path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"temperature": 0.9, "max_tokens": 1000}), encoding="utf-8")
    save_changes(p, {"voice_mode": VoiceMode.HOLD})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["voice_mode"] == "hold"
    assert data["temperature"] == 0.9    # pre-existing, differs from DEFAULTS, left intact
    assert data["max_tokens"] == 1000
    assert data["version"] == 1


def test_save_changes_creates_missing_file(tmp_path: Path):
    p = tmp_path / "sub" / "settings.json"
    save_changes(p, {"show_thinking": False})
    assert json.loads(p.read_text(encoding="utf-8"))["show_thinking"] is False


def test_top_p_none_roundtrips(tmp_path: Path):
    p = tmp_path / "settings.json"
    save_changes(p, {"top_p": None})
    assert load(p).top_p is None


def test_changed_fields_reports_only_diffs():
    a = DEFAULTS
    b = Settings(temperature=0.9, voice_mode=VoiceMode.HOLD)
    diff = changed_fields(a, b)
    assert diff == {"temperature": 0.9, "voice_mode": VoiceMode.HOLD}
    assert changed_fields(a, a) == {}


def test_sampling_fields_constant():
    assert SAMPLING_FIELDS == {"thinking_budget", "temperature", "top_p", "max_tokens"}
```

Append to `tests/test_paths.py`:

```python
def test_settings_path_under_user_data_dir():
    from llamatui import paths
    assert paths.settings_path().parent == paths.user_data_dir()
    assert paths.settings_path().name == "settings.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_settings.py tests/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'llamatui.settings'` (and `AttributeError` for `paths.settings_path`).

- [ ] **Step 3: Add `settings_path` to `paths.py`**

Append to `llamatui/paths.py`:

```python
def settings_path() -> Path:
    """Where the persisted Settings file lives (shares the per-user data root)."""
    return user_data_dir() / "settings.json"
```

- [ ] **Step 4: Create `llamatui/settings.py`**

```python
"""Settings — the global, persisted user preferences (the same for every conversation).

One of three buckets for state (see CONTEXT.md): Config is immutable bootstrap, Conversation is
per-chat, and Settings is the global preferences that survive restart. This module owns the
values, their precedence on load (CLI > saved file > built-in default), and field-level merge on
save. It is pure — no Textual, no agent, no keyboard — so its interface is its test surface.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields as _dataclass_fields
from enum import Enum
from pathlib import Path


class VoiceMode(str, Enum):
    TOGGLE = "toggle"
    HOLD = "hold"

    @classmethod
    def parse(cls, value) -> "VoiceMode":
        """Forgiving: a VoiceMode passes through; anything unrecognized → TOGGLE."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except (ValueError, AttributeError):
            return cls.TOGGLE


@dataclass(frozen=True)
class Settings:
    thinking_budget: int = 8192        # N>0 budget · 0 off · -1 unlimited
    temperature: float = 0.7
    top_p: float | None = None
    max_tokens: int = 32000
    voice_mode: VoiceMode = VoiceMode.TOGGLE
    show_thinking: bool = True

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "thinking_budget": self.thinking_budget,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_tokens,
            "voice_mode": self.voice_mode.value,
            "show_thinking": self.show_thinking,
        }


DEFAULTS = Settings()

SAMPLING_FIELDS = frozenset({"thinking_budget", "temperature", "top_p", "max_tokens"})

_FIELD_NAMES = frozenset(f.name for f in _dataclass_fields(Settings))


def from_dict(d: dict) -> Settings:
    """Build Settings from a (possibly partial / messy) dict. Missing keys fall to DEFAULTS,
    unknown keys are ignored, voice_mode parses forgivingly. Any bad field type → DEFAULTS
    wholesale rather than raising."""
    if not isinstance(d, dict):
        return DEFAULTS
    present = lambda k, default: d[k] if k in d else default
    try:
        return Settings(
            thinking_budget=int(present("thinking_budget", DEFAULTS.thinking_budget)),
            temperature=float(present("temperature", DEFAULTS.temperature)),
            top_p=(None if present("top_p", DEFAULTS.top_p) is None else float(d["top_p"])),
            max_tokens=int(present("max_tokens", DEFAULTS.max_tokens)),
            voice_mode=VoiceMode.parse(present("voice_mode", DEFAULTS.voice_mode)),
            show_thinking=bool(present("show_thinking", DEFAULTS.show_thinking)),
        )
    except (TypeError, ValueError):
        return DEFAULTS


def _read_file(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load(path: Path, cli: dict | None = None) -> Settings:
    """Resolve effective settings: DEFAULTS < saved file < non-None CLI overrides.
    Never writes the file."""
    merged = DEFAULTS.to_dict()
    merged.update(_read_file(path))
    if cli:
        for key, value in cli.items():
            if value is not None and key in _FIELD_NAMES:
                merged[key] = value
    return from_dict(merged)


def save_changes(path: Path, changed: dict) -> None:
    """Field-level merge: overlay only `changed` onto the existing file, re-stamp version, write.
    Persisting only what changed keeps a one-off CLI flag from leaking into the file."""
    data = _read_file(path)
    for key, value in changed.items():
        if key in _FIELD_NAMES:
            data[key] = value.value if isinstance(value, VoiceMode) else value
    data["version"] = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def changed_fields(old: Settings, new: Settings) -> dict:
    """Fields whose value differs old→new, as {name: new_value}."""
    return {
        name: getattr(new, name)
        for name in _FIELD_NAMES
        if getattr(old, name) != getattr(new, name)
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_settings.py tests/test_paths.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add llamatui/settings.py llamatui/paths.py tests/test_settings.py tests/test_paths.py
git commit -m "feat(settings): Settings model with CLI>file>default precedence and merge-on-save"
```

---

### Task 2: `parse_form` — panel input validation

**Files:**
- Modify: `llamatui/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces: `parse_form(raw: dict, base: Settings) -> tuple[Settings | None, dict]` — validates the four numeric text inputs; returns `(settings, {})` on success or `(None, {field: message})` on error. `base` carries the already-typed non-text fields (`voice_mode`, `show_thinking`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings.py`:

```python
from llamatui.settings import parse_form


def _raw(**over):
    base = {"thinking_budget": "8192", "temperature": "0.7", "top_p": "", "max_tokens": "32000"}
    base.update(over)
    return base


def test_parse_form_ok_blank_top_p_is_none():
    s, errors = parse_form(_raw(), DEFAULTS)
    assert errors == {}
    assert s.thinking_budget == 8192 and s.temperature == 0.7
    assert s.top_p is None and s.max_tokens == 32000


def test_parse_form_carries_base_nontext_fields():
    base = Settings(voice_mode=VoiceMode.HOLD, show_thinking=False)
    s, errors = parse_form(_raw(), base)
    assert s.voice_mode is VoiceMode.HOLD and s.show_thinking is False


def test_parse_form_top_p_value_parsed():
    s, errors = parse_form(_raw(top_p="0.95"), DEFAULTS)
    assert errors == {} and s.top_p == 0.95


def test_parse_form_rejects_non_numeric():
    s, errors = parse_form(_raw(temperature="hot"), DEFAULTS)
    assert s is None and "temperature" in errors


def test_parse_form_rejects_out_of_range():
    s, errors = parse_form(_raw(temperature="9"), DEFAULTS)
    assert s is None and "temperature" in errors


def test_parse_form_thinking_budget_allows_minus_one_but_not_minus_two():
    assert parse_form(_raw(thinking_budget="-1"), DEFAULTS)[0].thinking_budget == -1
    assert parse_form(_raw(thinking_budget="-2"), DEFAULTS)[1].get("thinking_budget")


def test_parse_form_max_tokens_must_be_positive():
    s, errors = parse_form(_raw(max_tokens="0"), DEFAULTS)
    assert s is None and "max_tokens" in errors
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_settings.py -k parse_form -v`
Expected: FAIL — `ImportError: cannot import name 'parse_form'`.

- [ ] **Step 3: Implement `parse_form`**

Append to `llamatui/settings.py` (add `replace` to the dataclass import: `from dataclasses import dataclass, fields as _dataclass_fields, replace`):

```python
def parse_form(raw: dict, base: Settings) -> "tuple[Settings | None, dict]":
    """Validate the panel's four numeric text inputs. Returns (settings, {}) on success or
    (None, {field: message}) on error. `base` supplies voice_mode / show_thinking, already typed
    by their RadioSet / Switch controls."""
    errors: dict = {}

    def _int(name, lo):
        text = str(raw.get(name, "")).strip()
        try:
            value = int(text)
        except ValueError:
            errors[name] = "must be a whole number"
            return None
        if value < lo:
            errors[name] = f"must be ≥ {lo}"
            return None
        return value

    def _float(name, lo, hi, allow_blank=False):
        text = str(raw.get(name, "")).strip()
        if allow_blank and text == "":
            return None
        try:
            value = float(text)
        except ValueError:
            errors[name] = "must be a number"
            return None
        if not (lo <= value <= hi):
            errors[name] = f"must be {lo}–{hi}"
            return None
        return value

    thinking_budget = _int("thinking_budget", lo=-1)
    temperature = _float("temperature", 0.0, 2.0)
    top_p = _float("top_p", 0.0, 1.0, allow_blank=True)
    max_tokens = _int("max_tokens", lo=1)

    if errors:
        return None, errors
    return (
        replace(
            base,
            thinking_budget=thinking_budget,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        ),
        {},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_settings.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add llamatui/settings.py tests/test_settings.py
git commit -m "feat(settings): parse_form panel validation with per-field errors"
```

---

### Task 3: `Dictation` gains `start` / `stop` / `cancel`

**Files:**
- Modify: `llamatui/dictation.py:79-129` (the `toggle` / `_start` / `_stop` region)
- Test: `tests/test_dictation.py`

**Interfaces:**
- Consumes: existing `Dictation`, `State`, fakes from `tests/test_dictation.py`.
- Produces, on `Dictation`:
  - `start() -> None` — idle→recording; no-op if recording/transcribing.
  - `stop() -> None` — recording→transcribing (with the existing min-duration guard); no-op otherwise.
  - `cancel() -> None` — recording→idle, closing the mic stream **without** transcribing; no-op if idle/transcribing.
  - `toggle()` unchanged in behaviour, now expressed via `start()`/`stop()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dictation.py`:

```python
def test_start_then_stop_transcribes():
    rec = FakeRecorder(pcm=_pcm(SAMPLE_RATE))
    stt = FakeTranscriber(text="explicit verbs")
    d, texts, states, notes = _make(rec, stt)
    d.start()
    assert d.state is State.RECORDING
    d.start()                                   # idempotent: still one recording
    assert d.state is State.RECORDING
    d.stop()
    assert d.state is State.IDLE
    assert texts == ["explicit verbs"]


def test_stop_while_idle_is_noop():
    rec = FakeRecorder(pcm=_pcm(SAMPLE_RATE))
    stt = FakeTranscriber()
    d, texts, *_ = _make(rec, stt)
    d.stop()                                    # nothing recording
    assert d.state is State.IDLE
    assert stt.last_wav is None


def test_cancel_discards_without_transcribing():
    rec = FakeRecorder(pcm=_pcm(SAMPLE_RATE))
    stt = FakeTranscriber()
    d, texts, *_ = _make(rec, stt)
    d.start()
    assert rec.started is True
    d.cancel()
    assert d.state is State.IDLE
    assert rec.started is False                 # mic stream closed
    assert texts == []
    assert stt.last_wav is None                 # never transcribed


def test_cancel_while_idle_is_noop():
    d, texts, *_ = _make(FakeRecorder(), FakeTranscriber())
    d.cancel()
    assert d.state is State.IDLE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dictation.py -k "start_then_stop or cancel or stop_while_idle" -v`
Expected: FAIL — `AttributeError: 'Dictation' object has no attribute 'start'`.

- [ ] **Step 3: Add the public verbs and re-express `toggle`**

In `llamatui/dictation.py`, replace the `toggle` method (currently at lines 79-85) with:

```python
    # ---- public verbs ---------------------------------------------------
    def start(self) -> None:
        """idle → recording; no-op if already recording or transcribing."""
        if self._state is State.IDLE:
            self._start()

    def stop(self) -> None:
        """recording → transcribing (min-duration guard applies); no-op otherwise."""
        if self._state is State.RECORDING:
            self._stop()

    def cancel(self) -> None:
        """recording → idle, discarding the audio without transcribing; no-op otherwise.
        Used when voice_mode changes mid-recording so the next dictation starts clean."""
        if self._state is State.RECORDING:
            self._rec.stop()
            self._set(State.IDLE)

    def toggle(self) -> None:
        if self._state is State.IDLE:
            self.start()
        elif self._state is State.RECORDING:
            self.stop()
        else:  # TRANSCRIBING
            self._on_note("still transcribing…")
```

(The existing private `_start` and `_stop` methods are unchanged.)

- [ ] **Step 4: Run the full dictation suite to verify pass + no regression**

Run: `uv run pytest tests/test_dictation.py -v`
Expected: PASS (new tests and all pre-existing ones).

- [ ] **Step 5: Commit**

```bash
git add llamatui/dictation.py tests/test_dictation.py
git commit -m "feat(dictation): public start/stop/cancel verbs alongside toggle"
```

---

### Task 4: Two-phase hold controller + OS key-repeat-delay helper

**Files:**
- Modify: `llamatui/app.py` (add near `_Debouncer`, around line 139-147)
- Test: `tests/test_app_hold.py`

**Interfaces:**
- Consumes: nothing (pure helpers).
- Produces, in `llamatui/app.py`:
  - `keyboard_initial_delay_s() -> float` — OS initial key-repeat delay in seconds (Windows `SPI_GETKEYBOARDDELAY`, 0–3 → 0.25/0.50/0.75/1.00 s), or `_DEFAULT_KEY_DELAY_S` (0.5) on any failure.
  - `HOLD_INITIAL_MARGIN_S = 0.30`, `HOLD_RELEASE_GAP_ACTIVE_S = 0.20`.
  - `class _HoldController(initial_delay_s: float)` with: property `recording: bool`; `on_key(now: float) -> bool` (True only on the first press → caller should start recording); `expired(now: float) -> bool` (True when the release gap has elapsed → caller should stop).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app_hold.py`:

```python
"""The hold-to-talk decision is a pure, clock-injected helper (like _Debouncer): it maps a
Ctrl+R auto-repeat burst to start/stop by inferring 'release' from a gap in the burst. Two-phase
gap — D+margin before the first repeat, a short gap after. See ADR-0002."""

from llamatui.app import (
    HOLD_INITIAL_MARGIN_S, HOLD_RELEASE_GAP_ACTIVE_S, _HoldController, keyboard_initial_delay_s,
)

D = 0.5  # injected initial delay for deterministic tests


def test_first_key_starts_and_sets_recording():
    h = _HoldController(D)
    assert h.recording is False
    assert h.on_key(0.0) is True            # first press → start
    assert h.recording is True
    assert h.on_key(0.0) is False           # subsequent keys never re-start


def test_before_first_repeat_waits_D_plus_margin():
    h = _HoldController(D)
    h.on_key(0.0)
    # only one keydown so far (no repeat seen): must wait D + margin before stopping
    assert h.expired(D + HOLD_INITIAL_MARGIN_S - 0.01) is False
    assert h.expired(D + HOLD_INITIAL_MARGIN_S + 0.01) is True
    assert h.recording is False


def test_repeat_confirmed_then_stops_on_short_gap_independent_of_D():
    h = _HoldController(D)
    h.on_key(0.0)               # start
    h.on_key(D)                 # first auto-repeat → confirms 'repeating'
    # now the active (short) gap applies, NOT D+margin
    assert h.expired(D + HOLD_RELEASE_GAP_ACTIVE_S - 0.01) is False
    assert h.expired(D + HOLD_RELEASE_GAP_ACTIVE_S + 0.01) is True


def test_long_hold_stream_stays_alive():
    h = _HoldController(D)
    h.on_key(0.0)
    t = D
    for _ in range(50):         # a long burst of repeats ~33 ms apart
        h.on_key(t)
        assert h.expired(t + 0.01) is False
        t += 0.033


def test_expired_is_false_when_not_recording():
    h = _HoldController(D)
    assert h.expired(100.0) is False


def test_keyboard_initial_delay_is_sane_float():
    v = keyboard_initial_delay_s()
    assert isinstance(v, float)
    assert 0.2 <= v <= 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_app_hold.py -v`
Expected: FAIL — `ImportError: cannot import name '_HoldController'`.

- [ ] **Step 3: Implement the helpers in `app.py`**

Add `import ctypes` to the imports block at the top of `llamatui/app.py`. Then add, just after the `_Debouncer` class (after line 147):

```python
_DEFAULT_KEY_DELAY_S = 0.5

# Hold-to-talk gaps. Before the first auto-repeat we must wait the OS initial-repeat delay D
# plus a margin (a held key is silent until repeat begins at ~D). Once a repeat confirms the
# burst is live, a short gap means release — crisp and independent of D. See ADR-0002.
HOLD_INITIAL_MARGIN_S = 0.30
HOLD_RELEASE_GAP_ACTIVE_S = 0.20


def keyboard_initial_delay_s() -> float:
    """OS initial key-repeat delay in seconds, or a safe default if unavailable.
    Windows SPI_GETKEYBOARDDELAY returns 0-3 → 250/500/750/1000 ms."""
    SPI_GETKEYBOARDDELAY = 0x0016
    try:
        value = ctypes.c_int()
        ok = ctypes.windll.user32.SystemParametersInfoW(
            SPI_GETKEYBOARDDELAY, 0, ctypes.byref(value), 0
        )
        if ok and 0 <= value.value <= 3:
            return 0.25 * (value.value + 1)
    except Exception:  # pragma: no cover - non-Windows / restricted env falls back
        pass
    return _DEFAULT_KEY_DELAY_S


class _HoldController:
    """Maps a Ctrl+R auto-repeat burst to record start/stop, inferring 'release' from a gap in
    the burst (terminals expose no key-release; see ADR-0002). Pure and clock-injected: the App
    feeds it key events and timer ticks and acts on the returned signals."""

    def __init__(self, initial_delay_s: float) -> None:
        self._before_gap = initial_delay_s + HOLD_INITIAL_MARGIN_S
        self._active_gap = HOLD_RELEASE_GAP_ACTIVE_S
        self._recording = False
        self._repeating = False
        self._last_key = 0.0

    @property
    def recording(self) -> bool:
        return self._recording

    def on_key(self, now: float) -> bool:
        """A Ctrl+R event. Returns True only on the first press (caller should start recording);
        later events are auto-repeat and confirm the burst is live."""
        self._last_key = now
        if not self._recording:
            self._recording = True
            self._repeating = False
            return True
        self._repeating = True
        return False

    def expired(self, now: float) -> bool:
        """Poll tick. Returns True once the release gap has elapsed (caller should stop)."""
        if not self._recording:
            return False
        gap = self._active_gap if self._repeating else self._before_gap
        if now - self._last_key > gap:
            self._recording = False
            self._repeating = False
            return True
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_app_hold.py -v`
Expected: PASS (all). `test_keyboard_initial_delay_is_sane_float` passes on Windows via the real query and elsewhere via the fallback.

- [ ] **Step 5: Commit**

```bash
git add llamatui/app.py tests/test_app_hold.py
git commit -m "feat(voice): two-phase hold controller + SPI_GETKEYBOARDDELAY helper"
```

---

### Task 5: Agent-build split, `Config` slim, `Settings` wiring (app + main)

**Files:**
- Modify: `llamatui/app.py` (`Config` class ~150-169; `LlamaTUI.__init__` ~186-203; `on_mount` ~218; `_rebuild_agent` ~319-350)
- Modify: `llamatui/__main__.py` (args + `Config` call + launch)
- Test: `tests/test_main_overrides.py`

**Interfaces:**
- Consumes: `settings.load`, `settings.Settings`, `paths.settings_path` (Task 1).
- Produces:
  - `Config` no longer carries `temperature`, `max_tokens`, `top_p`, `thinking_budget`.
  - `LlamaTUI(config, cli_overrides: dict | None = None)`; `self.settings: Settings` set in `on_mount`; `self._instructions: str` and `self._tools: list` caches.
  - `LlamaTUI._build_instructions() -> None` (sets `self._instructions` + `self._tools`); `LlamaTUI._apply_agent() -> None` (builds `self.agent` from caches + `self.settings`); `_rebuild_agent()` calls both.
  - `__main__.cli_overrides(args) -> dict` mapping argparse `Namespace` → the `cli` dict for `settings.load`.

- [ ] **Step 1: Write the failing test for the CLI→overrides mapping**

Create `tests/test_main_overrides.py`:

```python
"""__main__.cli_overrides maps argparse results to the precedence dict settings.load expects:
an unset flag is None (a sentinel that must not clobber the saved file)."""

from argparse import Namespace

from llamatui.__main__ import cli_overrides


def _args(**over):
    base = dict(thinking_budget=None, temp=None, top_p=None, max_tokens=None, voice_mode=None)
    base.update(over)
    return Namespace(**base)


def test_unset_flags_map_to_none():
    assert cli_overrides(_args()) == {
        "thinking_budget": None, "temperature": None, "top_p": None,
        "max_tokens": None, "voice_mode": None,
    }


def test_set_flags_pass_through():
    out = cli_overrides(_args(temp=0.9, voice_mode="hold"))
    assert out["temperature"] == 0.9
    assert out["voice_mode"] == "hold"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_main_overrides.py -v`
Expected: FAIL — `ImportError: cannot import name 'cli_overrides'`.

- [ ] **Step 3: Slim `Config` and split the agent build in `app.py`**

In `llamatui/app.py`, replace the `Config.__init__` (lines 150-169) with the sampling fields removed:

```python
class Config:
    def __init__(
        self, url, model, system, db_path=None, web=True, memory=True,
        voice=True, whisper_bin=None, whisper_model=None, whisper_url=None,
    ):
        self.url = url
        self.model = model
        self.system = system
        self.db_path = db_path
        self.web = web
        self.memory = memory
        self.voice = voice
        self.whisper_bin = whisper_bin
        self.whisper_model = whisper_model
        self.whisper_url = whisper_url
```

Update `LlamaTUI.__init__` signature and body — accept `cli_overrides`, drop `self.show_thinking`, add caches and the hold state. Replace lines 186-203:

```python
    def __init__(self, config: Config, cli_overrides: dict | None = None) -> None:
        super().__init__()
        self.config = config
        self._cli_overrides = cli_overrides or {}
        self.settings = DEFAULTS                 # replaced with the resolved value in on_mount
        self.model_label = humanize_model_name(config.model)
        self.context_window: int | None = None
        self.agent = None
        self._instructions: str = ""
        self._tools: list = []
        self._busy = False
        self.store: Store | None = None
        self.conversation: Conversation | None = None
        self.web_tool = None
        self.web_enabled = False
        self.memory: Memory | None = None
        self.whisper: WhisperServer | None = None
        self.dictation: Dictation | None = None
        self.voice_enabled = False
        self._cap_timer = None
        self._dictate_debounce = _Debouncer(DICTATE_DEBOUNCE_S)
        self._key_delay_s = keyboard_initial_delay_s()
        self._hold = _HoldController(self._key_delay_s)
        self._hold_timer = None
```

Add the new imports near the other local imports (top of `app.py`):

```python
from . import paths
from .settings import (
    DEFAULTS, SAMPLING_FIELDS, Settings, VoiceMode, changed_fields, load as load_settings,
    save_changes,
)
```

(Remove the now-redundant `from .paths import default_whisper_dir` line and use `paths.default_whisper_dir()` at its call site in `resolve_whisper_dir`, or keep both imports — simplest is to add `from . import paths` and leave the existing `default_whisper_dir` import in place.)

At the very start of `on_mount` (after `async def on_mount(self) -> None:`), resolve settings before anything uses them:

```python
        self.settings = load_settings(paths.settings_path(), self._cli_overrides)
```

Replace `_rebuild_agent` (lines 319-350) with the split:

```python
    def _build_instructions(self) -> None:
        """Compose the semi-volatile system prompt + gather the conversation-stable tools.
        Called only at conversation boundaries; the result is cached so a mid-conversation
        sampling change can rebuild the agent without recomputing the prompt (cache prefix)."""
        tools: list = []
        tool_notes: list[str] = []
        if self.web_enabled:
            tools.append(self.web_tool)
            tool_notes.append(WEB_SEARCH_GUIDANCE)
        ambient = None
        if self.memory is not None:
            tools.extend(self.memory.build_tools())
            tool_notes.append(MEMORY_GUIDANCE)
            ambient = self.memory.preamble()
        capabilities = (
            ["Your tools (use them deliberately):\n\n" + "\n\n".join(tool_notes)] if tool_notes else []
        )
        self._instructions = build_instructions(
            persona=self.conversation.system_prompt or DEFAULT_SYSTEM,
            capabilities=capabilities,
            ambient=ambient,
            volatile=_date_line(),
        )
        self._tools = tools

    def _apply_agent(self) -> None:
        """Build the agent from the cached instructions/tools + current sampling settings.
        Safe mid-stream: the generate worker holds the old agent's iterator (see CONTEXT.md)."""
        self.agent = build_agent(
            base_url=self.config.url,
            model=self.config.model,
            instructions=self._instructions or None,
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            top_p=self.settings.top_p,
            thinking_budget=self.settings.thinking_budget,
            tools=self._tools or None,
        )

    def _rebuild_agent(self) -> None:
        self._build_instructions()
        self._apply_agent()
```

- [ ] **Step 4: Update `__main__.py` — sentinel defaults, `--voice-mode`, `cli_overrides`, launch**

In `llamatui/__main__.py`, change the four sampling args to `default=None` and add `--voice-mode`. Replace lines 18-24 and add one line:

```python
    ap.add_argument("--temp", type=float, default=None, help="sampling temperature (default: 0.7)")
    ap.add_argument("--max-tokens", type=int, default=None, help="max tokens to generate (default: 32000)")
    ap.add_argument("--top-p", type=float, default=None, help="nucleus sampling probability (default: off)")
    ap.add_argument(
        "--thinking-budget", type=int, default=None,
        help="max thinking tokens (default: 8192; N>0 budget, 0 off, -1 unlimited). "
             "Honored only if llama-server was started without --reasoning-budget.",
    )
    ap.add_argument("--voice-mode", choices=["toggle", "hold"], default=None,
                    help="dictation input mode (default: toggle)")
```

Add a module-level helper above `main()`:

```python
def cli_overrides(args) -> dict:
    """Map parsed args to the precedence dict settings.load expects (unset → None sentinel)."""
    return {
        "thinking_budget": args.thinking_budget,
        "temperature": args.temp,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "voice_mode": args.voice_mode,
    }
```

Replace the `Config(...)` construction and launch (lines 48-64) with the slimmed config + overrides:

```python
    config = Config(
        url=base_url,
        model=args.model,
        system=args.system,
        db_path=args.db,
        web=not args.no_web,
        memory=not args.no_memory,
        voice=not args.no_voice,
        whisper_bin=args.whisper_bin,
        whisper_model=args.whisper_model,
        whisper_url=args.whisper_url,
    )
    LlamaTUI(config, cli_overrides=cli_overrides(args)).run()
```

- [ ] **Step 5: Run the targeted test + the full suite**

Run: `uv run pytest tests/test_main_overrides.py -v && uv run pytest -q`
Expected: the override tests PASS; the full suite PASS (existing `test_app_resolve`, `test_main_setup_voice`, `test_instructions`, etc. remain green — the agent-build split is behavior-preserving).

- [ ] **Step 6: Commit**

```bash
git add llamatui/app.py llamatui/__main__.py tests/test_main_overrides.py
git commit -m "refactor(app): split agent build (cached instructions + apply); move sampling into Settings"
```

---

### Task 6: `SettingsScreen` modal, opener binding, voice-mode dispatch, remove `Ctrl+T`/`/think`

**Files:**
- Create: `llamatui/settings_screen.py`
- Modify: `llamatui/app.py` (BINDINGS ~176-184; `HELP` ~42-47; `_handle_command` ~391-407; `action_toggle_thinking` ~614-620; `action_dictate`/`_cap_stop` ~577-595; `_send` ~412 and `open_conversation` ~525 `AssistantTurn(...)` construction)
- Modify: `llamatui/styles.tcss` (append modal styles)
- Test: manual smoke (Textual modal + on_mount network make App-level unit tests impractical here; the validation logic is already covered by `parse_form` tests in Task 2).

**Interfaces:**
- Consumes: `Settings`, `VoiceMode`, `parse_form` (Tasks 1-2); `changed_fields`, `SAMPLING_FIELDS`, `save_changes`, `paths.settings_path` (Task 1/5); `_HoldController`, `keyboard_initial_delay_s` (Task 4); `Dictation.start/stop/cancel` (Task 3).
- Produces: `class SettingsScreen(ModalScreen[Settings | None])` returning the edited `Settings` (or `None`); `LlamaTUI.action_open_settings`, `_on_settings_closed`, `_dictate_toggle`, `_dictate_hold`, `_hold_tick`, `_stop_hold_timer`.

- [ ] **Step 1: Create `llamatui/settings_screen.py`**

```python
"""SettingsScreen — the modal settings panel. Takes the current Settings, returns the edited
Settings on Save (or None on Cancel). All validation lives in settings.parse_form, so this file
is thin Textual glue; the screen never touches the agent, the file, or the App."""

from __future__ import annotations

from dataclasses import replace

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static, Switch

from .settings import Settings, VoiceMode, parse_form


class SettingsScreen(ModalScreen["Settings | None"]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current: Settings) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        s = self._current
        with Vertical(id="settings-box"):
            yield Static("Settings", id="settings-title")
            yield Label("Thinking budget  (N>0 budget · 0 off · -1 unlimited)")
            yield Input(value=str(s.thinking_budget), id="thinking_budget")
            yield Label("Temperature  (0.0–2.0)")
            yield Input(value=str(s.temperature), id="temperature")
            yield Label("Top-p  (0.0–1.0; blank = off)")
            yield Input(value="" if s.top_p is None else str(s.top_p), id="top_p")
            yield Label("Max tokens")
            yield Input(value=str(s.max_tokens), id="max_tokens")
            yield Label("Voice input mode")
            with RadioSet(id="voice_mode"):
                yield RadioButton("Toggle — press to start/stop", value=s.voice_mode is VoiceMode.TOGGLE)
                yield RadioButton("Hold — hold to talk", value=s.voice_mode is VoiceMode.HOLD)
            with Horizontal(id="show-thinking-row"):
                yield Label("Show thinking panes")
                yield Switch(value=s.show_thinking, id="show_thinking")
            yield Static("", id="settings-error")
            with Horizontal(id="settings-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self._save()

    def _save(self) -> None:
        raw = {
            "thinking_budget": self.query_one("#thinking_budget", Input).value,
            "temperature": self.query_one("#temperature", Input).value,
            "top_p": self.query_one("#top_p", Input).value,
            "max_tokens": self.query_one("#max_tokens", Input).value,
        }
        radio = self.query_one("#voice_mode", RadioSet)
        voice = VoiceMode.HOLD if radio.pressed_index == 1 else VoiceMode.TOGGLE
        show = self.query_one("#show_thinking", Switch).value
        base = replace(self._current, voice_mode=voice, show_thinking=show)
        result, errors = parse_form(raw, base)
        if errors:
            message = "   ".join(f"{name}: {msg}" for name, msg in errors.items())
            self.query_one("#settings-error", Static).update(f"[red]{message}[/]")
            return
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 2: Wire the opener, close-handler, and voice dispatch into `app.py`**

Add the import near the other local imports in `llamatui/app.py`:

```python
from .settings_screen import SettingsScreen
```

Replace the `BINDINGS` list (lines 176-184) — drop `ctrl+t`, add `ctrl+,`:

```python
    BINDINGS = [
        Binding("ctrl+n", "new_chat", "New"),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+comma", "open_settings", "Settings"),
        Binding("ctrl+d", "delete_chat", "Delete"),
        Binding("ctrl+r", "dictate", "Dictate"),
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+q", "quit", "Quit"),
    ]
```

Replace `action_toggle_thinking` (lines 614-620) entirely with the settings opener + close handler:

```python
    def action_open_settings(self) -> None:
        self.push_screen(SettingsScreen(self.settings), self._on_settings_closed)

    def _on_settings_closed(self, result: "Settings | None") -> None:
        if result is None:
            return
        changed = changed_fields(self.settings, result)
        if not changed:
            return
        self.settings = result
        if changed.keys() & SAMPLING_FIELDS:
            self._apply_agent()                       # cached instructions → cache prefix intact
        if "show_thinking" in changed:
            for turn in self.query(AssistantTurn):
                turn.set_thinking_visible(result.show_thinking)
        if "voice_mode" in changed and self.dictation is not None:
            self.dictation.cancel()                   # discard any in-flight recording
            self._stop_hold_timer()
            self._hold = _HoldController(self._key_delay_s)
        save_changes(paths.settings_path(), changed)
```

Replace `action_dictate` and `_cap_stop` (lines 577-595) with the mode-aware version:

```python
    def action_dictate(self) -> None:
        if self.dictation is None:
            self._voice_note("voice off — run: llamatui --setup-voice")
            return
        if self.settings.voice_mode is VoiceMode.HOLD:
            self._dictate_hold()
        else:
            self._dictate_toggle()

    def _dictate_toggle(self) -> None:
        # Collapse held-key auto-repeat to a single toggle (see _Debouncer).
        if not self._dictate_debounce.should_fire(time.monotonic()):
            return
        self.dictation.toggle()
        if self.dictation.state is State.RECORDING:
            self._cap_timer = self.set_timer(120.0, self._cap_stop)
        elif self._cap_timer is not None:
            self._cap_timer.stop()
            self._cap_timer = None

    def _dictate_hold(self) -> None:
        if self._hold.on_key(time.monotonic()):       # first press → start recording
            self.dictation.start()
            self._cap_timer = self.set_timer(120.0, self._cap_stop)
            self._hold_timer = self.set_interval(0.05, self._hold_tick)

    def _hold_tick(self) -> None:
        if self._hold.expired(time.monotonic()):
            self._stop_hold_timer()
            if self._cap_timer is not None:
                self._cap_timer.stop()
                self._cap_timer = None
            if self.dictation is not None:
                self.dictation.stop()

    def _stop_hold_timer(self) -> None:
        if self._hold_timer is not None:
            self._hold_timer.stop()
            self._hold_timer = None

    def _cap_stop(self) -> None:
        self._cap_timer = None
        self._stop_hold_timer()
        if self.dictation is not None and self.dictation.state is State.RECORDING:
            self._voice_note("recording stopped (max length)")
            self.dictation.stop()
```

- [ ] **Step 3: Remove `/think` from `HELP` and `_handle_command`; read `show_thinking` from settings**

In `HELP` (lines 42-47), delete the `/think` line so it reads:

```python
HELP = """[b]commands[/b]
  [cyan]/new[/]              start a new conversation (also Ctrl+N)
  [cyan]/system <text>[/]    set or replace the system prompt
  [cyan]/help[/]             this list
  [cyan]/exit[/], [cyan]/quit[/]      leave"""
```

In `_handle_command` (lines 391-407), delete the `elif cmd == "/think":` branch and its body.

In `_send` (line 412) and `open_conversation` (line 525), change `AssistantTurn(show_thinking=self.show_thinking)` to `AssistantTurn(show_thinking=self.settings.show_thinking)` (both occurrences).

- [ ] **Step 4: Append modal styles to `styles.tcss`**

Append to `llamatui/styles.tcss`:

```css
SettingsScreen {
    align: center middle;
}

#settings-box {
    width: 64;
    height: auto;
    padding: 1 2;
    background: $surface;
    border: round $primary;
}

#settings-title {
    text-style: bold;
    width: 100%;
    content-align: center middle;
    margin-bottom: 1;
}

#settings-box Input {
    margin-bottom: 1;
}

#show-thinking-row {
    height: auto;
    margin-bottom: 1;
}

#settings-error {
    height: auto;
    margin-bottom: 1;
}

#settings-buttons {
    height: auto;
    align-horizontal: right;
}

#settings-buttons Button {
    margin-left: 2;
}
```

- [ ] **Step 5: Run the full suite (no regressions from the app edits)**

Run: `uv run pytest -q`
Expected: PASS (the removed `/think`/`Ctrl+T` aren't referenced by tests; `show_thinking` now lives on settings).

- [ ] **Step 6: Manual smoke verification**

With a llama-server running (per `run-llama-server.ps1`), run `uv run llamatui` and verify:
1. Footer shows **Settings** on `Ctrl+,`; pressing it opens the modal.
2. Edit thinking budget to `0`, Save → next reply has no thinking pane; reopen panel → shows `0`.
3. Type `abc` in Temperature, Save → inline red error, panel stays open; fix it → saves.
4. Toggle "Show thinking panes" off, Save, quit, relaunch → thinking stays hidden (persisted).
5. Set Voice input mode to **Hold**, Save → hold `Ctrl+R`, speak, release → transcript appears ~0.2s after release. Switch back to **Toggle** → press/press works.
6. Confirm `%LOCALAPPDATA%\llamatui\settings.json` contains only the fields you changed.

- [ ] **Step 7: Commit**

```bash
git add llamatui/settings_screen.py llamatui/app.py llamatui/styles.tcss
git commit -m "feat(settings): modal panel (Ctrl+,), voice-mode dispatch; remove Ctrl+T and /think"
```

---

### Task 7: Documentation — README + CONTEXT pointer

**Files:**
- Modify: `README.md` (the keybindings table line for `Ctrl+T`; the commands list line for `/think`; add settings panel + `--voice-mode`)
- Modify: `CONTEXT.md` (the architecture-stance note on the agent-build split, if not already present)

**Interfaces:** none (docs).

- [ ] **Step 1: Update `README.md`**

- Remove the `| `Ctrl+T` | collapse/expand thinking panes |` row from the keybindings table (around line 141) and add a row: `| `Ctrl+,` | open the settings panel |`.
- Remove the `- `/think` — toggle whether thinking panes are shown` line from the commands list (around line 152).
- In the keybindings/usage area, add a short note: "Runtime preferences — thinking budget, temperature, top-p, max tokens, voice input mode (toggle vs hold-to-talk), and thinking-pane visibility — live in the settings panel (`Ctrl+,`) and persist to `%LOCALAPPDATA%\llamatui\settings.json`. CLI flags override the saved values for one run. Note: the thinking-budget setting is honored only when llama-server was started without `--reasoning-budget`."
- In the CLI flags documentation, add `--voice-mode {toggle,hold}` and note the sampling flags now default from the saved settings file.

- [ ] **Step 2: Add the agent-build-split note to `CONTEXT.md`**

Under the **Architecture stance** section's cache-prefix paragraph in `CONTEXT.md`, append:

```markdown
The agent build is split for this: `_build_instructions` composes the (semi-volatile) system
prompt and caches it + the conversation-stable tools at conversation boundaries only;
`_apply_agent` rebuilds the agent from those caches plus the current **Settings** sampling. A
mid-conversation sampling change calls `_apply_agent` alone, so the prompt — and its KV prefix —
never changes. This is also why opening the settings panel mid-stream is safe.
```

- [ ] **Step 3: Verify docs reference nothing removed**

Run: `grep -rn "Ctrl+T\|/think" README.md`
Expected: no matches (both removed).

- [ ] **Step 4: Commit**

```bash
git add README.md CONTEXT.md
git commit -m "docs: settings panel, Ctrl+, , --voice-mode; remove Ctrl+T/think; agent-split note"
```

---

## Self-Review

**Spec coverage check (each spec section → task):**
- `Settings` module / precedence / persistence / `save_changes` / `changed_fields` / `SAMPLING_FIELDS` → Task 1. ✅
- Panel validation (`parse_form`) → Task 2. ✅
- `Dictation.start/stop/cancel` → Task 3. ✅
- Two-phase hold controller + `SPI_GETKEYBOARDDELAY` → Task 4. ✅
- `_build_instructions`/`_apply_agent` split; `Config` slim; sentinel CLI defaults; `--voice-mode`; mid-stream safety → Task 5. ✅
- `SettingsScreen`, `Ctrl+,` opener, `_on_settings_closed` (diff → apply → merge-save), voice-mode dispatch, cancel-on-mode-change, remove `Ctrl+T`/`/think` → Task 6. ✅
- `paths.settings_path` → Task 1. ✅
- README + CONTEXT → Task 7 (CONTEXT glossary entry for Settings/voice-mode already committed in the design phase). ✅
- Tests: `test_settings.py`, hold controller, dictation verbs, CLI overrides → Tasks 1-5. ✅

**Placeholder scan:** no TBD/TODO; every code step shows complete code; the one manual-verification step (Task 6 Step 6) is explicit because App-level Textual + networked `on_mount` make a unit test impractical — the underlying logic is covered by `parse_form`, `changed_fields`, and `_HoldController` unit tests.

**Type consistency:** `Settings`, `VoiceMode`, `parse_form(raw, base) -> (Settings|None, dict)`, `changed_fields(old,new) -> dict`, `SAMPLING_FIELDS` (set of field-name strings, matched via `changed.keys() & SAMPLING_FIELDS`), `_HoldController.on_key/expired/recording`, `keyboard_initial_delay_s() -> float`, `cli_overrides(args) -> dict`, `Dictation.start/stop/cancel` — names and signatures are identical across the tasks that define and consume them.

**Known deferral (matches spec non-goals):** runtime web/memory/voice feature on/off is out of scope; `Settings` accommodates new fields without restructuring.
