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
