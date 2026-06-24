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


def test_from_dict_non_dict_is_defaults():
    assert from_dict("not a dict") == DEFAULTS
    assert from_dict(None) == DEFAULTS


def test_save_changes_ignores_unknown_keys(tmp_path):
    p = tmp_path / "settings.json"
    save_changes(p, {"bogus": 1, "temperature": 0.4})
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "bogus" not in data
    assert data["temperature"] == 0.4


def test_save_changes_is_atomic_no_temp_left(tmp_path):
    p = tmp_path / "settings.json"
    save_changes(p, {"temperature": 0.4})
    assert p.exists()
    assert not (tmp_path / "settings.json.tmp").exists()
    assert load(p).temperature == 0.4


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
