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
    merged = {k: v for k, v in DEFAULTS.to_dict().items() if k != "version"}
    merged.update(_read_file(path))
    if cli is not None:
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
