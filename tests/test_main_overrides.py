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
