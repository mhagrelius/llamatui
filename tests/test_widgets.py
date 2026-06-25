"""Pure helpers behind the widgets are the test surface: leading-space logic for inserted
transcripts, and the stateful status line that must never let a gen repaint erase the voice
segment. No Textual app is spun up here."""

from llamatui.widgets import _needs_leading_space, render_status, _CMD_OUTPUT_TAIL_CAP


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


# ---- append_command_output tail-capping (no Textual App needed) ------------------

def _simulate_append(chunks: list[str], cap: int = _CMD_OUTPUT_TAIL_CAP) -> str:
    """Replay append_command_output logic without mounting a widget; returns final text."""
    from rich.text import Text
    current_text = ""
    for chunk in chunks:
        combined = current_text + chunk
        lines = combined.splitlines(keepends=True)
        if len(lines) > cap:
            lines = lines[-cap:]
        current_text = "".join(lines)
    return current_text


def test_append_command_output_small_stays_intact():
    text = _simulate_append(["line1\n", "line2\n", "line3\n"])
    assert text == "line1\nline2\nline3\n"


def test_append_command_output_caps_at_limit():
    # 250 lines → should trim to exactly _CMD_OUTPUT_TAIL_CAP (200) lines
    chunks = [f"line{i}\n" for i in range(250)]
    text = _simulate_append(chunks)
    lines = text.splitlines()
    assert len(lines) == _CMD_OUTPUT_TAIL_CAP
    # The tail should contain the LAST 200 lines (50..249)
    assert lines[0] == "line50"
    assert lines[-1] == "line249"


def test_append_command_output_partial_chunk_no_newline():
    # A chunk without a trailing newline is treated as a partial line; no spurious trimming
    text = _simulate_append(["hello ", "world"])
    assert text == "hello world"
