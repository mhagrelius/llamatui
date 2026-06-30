"""SettingsScreen — the modal settings panel. Takes the current Settings, returns the edited
Settings on Save (or None on Cancel). All validation lives in settings.parse_form, so this file
is thin Textual glue; the screen never touches the agent, the file, or the App."""

from __future__ import annotations

from dataclasses import replace
from typing import NamedTuple

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static, Switch

from .settings import Settings, VoiceMode, parse_form


class _Input(NamedTuple):
    """One text field in the panel. `placeholder` shows when the field is empty (so it doubles as
    the field's hint). The field's value is read from the Settings attribute named `id`."""

    id: str
    label: str
    placeholder: str = ""


# The panel's text fields, in display order. parse_form validates the five numeric ones by id;
# default_workspace is folded in here too but handled specially in _save (blank → None). This
# table is the single source of truth: compose() builds a row per entry and _save() reads the
# same ids back, so the two can never drift.
_INPUTS: tuple[_Input, ...] = (
    _Input("thinking_budget", "Thinking budget", placeholder="8192 · 0 off · -1 ∞"),
    _Input("temperature", "Temperature", placeholder="0.0–2.0"),
    _Input("top_p", "Top-p", placeholder="off"),
    _Input("max_tokens", "Max tokens", placeholder="32000"),
    _Input("keep_recent_turns", "Keep recent turns", placeholder="kept uncompacted"),
    _Input("default_workspace", "Default workspace", placeholder="none"),
)

# Boolean toggles, in display order: (Settings attribute / widget id, label).
_TOGGLES: tuple[tuple[str, str], ...] = (
    ("show_thinking", "Show thinking panes"),
    ("compaction_enabled", "Auto-compaction"),
    ("llm_summary", "LLM summarization"),
)


class SettingsScreen(ModalScreen["Settings | None"]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, current: Settings) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        s = self._current
        with Vertical(id="settings-box"):
            yield Static("Settings", id="settings-title")
            with VerticalScroll(id="settings-fields"):
                for f in _INPUTS:
                    with Horizontal(classes="field-row"):
                        yield Label(f.label, classes="field-label")
                        yield Input(
                            value=self._value_for(f.id),
                            placeholder=f.placeholder,
                            id=f.id,
                            classes="field-input",
                        )
                with Horizontal(classes="field-row"):
                    yield Label("Voice input", classes="field-label")
                    with RadioSet(id="voice_mode", classes="field-radio"):
                        yield RadioButton("Toggle", value=s.voice_mode is VoiceMode.TOGGLE)
                        yield RadioButton("Hold", value=s.voice_mode is VoiceMode.HOLD)
                for attr, label in _TOGGLES:
                    with Horizontal(classes="field-row toggle-row"):
                        yield Label(label, classes="toggle-label")
                        yield Switch(value=getattr(s, attr), id=attr, classes="field-switch")
            yield Static("", id="settings-error")
            with Horizontal(id="settings-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def _value_for(self, attr: str) -> str:
        value = getattr(self._current, attr)
        return "" if value is None else str(value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self._save()

    def _save(self) -> None:
        raw = {f.id: self.query_one(f"#{f.id}", Input).value for f in _INPUTS}
        radio = self.query_one("#voice_mode", RadioSet)
        voice = VoiceMode.HOLD if radio.pressed_index == 1 else VoiceMode.TOGGLE
        toggles = {attr: self.query_one(f"#{attr}", Switch).value for attr, _ in _TOGGLES}
        workspace = raw["default_workspace"].strip() or None
        base = replace(self._current, voice_mode=voice, default_workspace=workspace, **toggles)
        result, errors = parse_form(raw, base)
        if errors:
            message = "   ".join(f"{name}: {msg}" for name, msg in errors.items())
            self.query_one("#settings-error", Static).update(message)
            return
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)
