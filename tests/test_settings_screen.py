"""The settings panel's field tables (_INPUTS / _TOGGLES) are the single source of truth for
which controls exist and which Settings attribute each one binds to. compose() builds rows from
them and _save() reads the same ids back, so these pure checks (no Textual app) guard that the
table stays consistent with parse_form's contract and the Settings dataclass — relabeling the
panel can then never silently drop a field _save() depends on."""

from dataclasses import fields as dataclass_fields

from llamatui.settings import Settings
from llamatui.settings_screen import _INPUTS, _TOGGLES


def _settings_attrs() -> set[str]:
    return {f.name for f in dataclass_fields(Settings)}


def test_every_input_binds_to_a_settings_attribute():
    attrs = _settings_attrs()
    for inp in _INPUTS:
        assert inp.id in attrs, f"input {inp.id!r} is not a Settings field"


def test_every_toggle_binds_to_a_settings_attribute():
    attrs = _settings_attrs()
    for attr, _label in _TOGGLES:
        assert attr in attrs, f"toggle {attr!r} is not a Settings field"


def test_parse_form_numeric_fields_are_all_rendered():
    # The five ids parse_form validates must each have a rendered Input; otherwise _save would
    # build a raw dict missing them and parse_form would reject otherwise-valid input.
    rendered = {f.id for f in _INPUTS}
    required = {"thinking_budget", "temperature", "top_p", "max_tokens", "keep_recent_turns"}
    assert required <= rendered


def test_default_workspace_is_rendered():
    assert "default_workspace" in {f.id for f in _INPUTS}


def test_all_widget_ids_are_unique():
    ids = [f.id for f in _INPUTS] + [attr for attr, _ in _TOGGLES]
    assert len(ids) == len(set(ids))
