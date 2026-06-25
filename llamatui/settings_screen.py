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
            yield Label("Default workspace  (path, blank = none)")
            yield Input(value=s.default_workspace or "", id="default_workspace")
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
        ws_raw = self.query_one("#default_workspace", Input).value.strip()
        workspace = ws_raw if ws_raw else None
        base = replace(self._current, voice_mode=voice, show_thinking=show, default_workspace=workspace)
        result, errors = parse_form(raw, base)
        if errors:
            message = "   ".join(f"{name}: {msg}" for name, msg in errors.items())
            self.query_one("#settings-error", Static).update(f"[red]{message}[/]")
            return
        self.dismiss(result)

    def action_cancel(self) -> None:
        self.dismiss(None)
