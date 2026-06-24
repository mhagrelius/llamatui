"""Pure helpers behind the widgets are the test surface: leading-space logic for inserted
transcripts, and the stateful status line that must never let a gen repaint erase the voice
segment. No Textual app is spun up here."""

from llamatui.widgets import _needs_leading_space, render_status


def test_needs_leading_space_after_word():
    assert _needs_leading_space("g") is True          # "...the bug" + dictation

def test_no_leading_space_at_start_or_after_space():
    assert _needs_leading_space("") is False
    assert _needs_leading_space(" ") is False
    assert _needs_leading_space("\n") is False

def test_no_leading_space_after_opener():
    assert _needs_leading_space("(") is False
    assert _needs_leading_space('"') is False


def test_render_status_includes_all_segments():
    t = render_status(model="qwen", state="ready", detail="ctx 10k", connected=True, voice="🎙 recording")
    plain = t.plain
    assert "qwen" in plain and "ready" in plain and "ctx 10k" in plain and "🎙 recording" in plain

def test_render_status_omits_empty_voice():
    t = render_status(model="qwen", state="ready", detail="", connected=True, voice="")
    assert "🎙" not in t.plain
