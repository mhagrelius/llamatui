"""__main__.cli_overrides maps argparse results to the precedence dict settings.load expects:
an unset flag is None (a sentinel that must not clobber the saved file)."""

from argparse import Namespace

from llamatui.__main__ import cli_overrides


def _args(**over):
    base = dict(thinking_budget=None, temp=None, top_p=None, max_tokens=None, voice_mode=None,
                workspace=None,
                no_compaction=False, keep_recent_turns=None, no_llm_summary=False)
    base.update(over)
    return Namespace(**base)


def test_unset_flags_map_to_none():
    assert cli_overrides(_args()) == {
        "thinking_budget": None, "temperature": None, "top_p": None,
        "max_tokens": None, "voice_mode": None, "default_workspace": None,
        "compaction_enabled": None, "keep_recent_turns": None, "llm_summary": None,
    }


def test_set_flags_pass_through():
    out = cli_overrides(_args(temp=0.9, voice_mode="hold"))
    assert out["temperature"] == 0.9
    assert out["voice_mode"] == "hold"


def test_cli_overrides_compaction_defaults_to_none():
    o = cli_overrides(_args())
    assert o["compaction_enabled"] is None
    assert o["keep_recent_turns"] is None
    assert o["llm_summary"] is None


def test_cli_overrides_no_compaction_sets_false():
    o = cli_overrides(_args(no_compaction=True))
    assert o["compaction_enabled"] is False


def test_cli_overrides_keep_recent_and_no_llm():
    o = cli_overrides(_args(keep_recent_turns=3, no_llm_summary=True))
    assert o["keep_recent_turns"] == 3
    assert o["llm_summary"] is False
