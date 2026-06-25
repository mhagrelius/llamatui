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


def test_render_status_includes_workspace_path():
    t = render_status(
        model="qwen", state="ready", detail="", connected=True, voice="",
        workspace="C:/Users/Matthew",
    )
    assert "C:/Users/Matthew" in t.plain


def test_render_status_omits_empty_workspace():
    t = render_status(model="qwen", state="ready", detail="ctx 3%", connected=True, voice="")
    assert "C:" not in t.plain  # no workspace segment renders when none is provided


# ---- append_command_output tail-capping (no Textual App needed) ------------------
# Tests call the REAL AssistantTurn.append_command_output end-to-end.
# We stub only what needs a running App: query_one returns a fake tools container
# whose mount() swaps the freshly-created Static for a lightweight _FakeTailStatic
# (so _cmd_tail.update() is captured without a Textual render loop).

class _FakeTailStatic:
    """Minimal stand-in for Textual Static — records the last update() call."""
    def __init__(self):
        self.content = None
    def update(self, text):
        self.content = text


def _run_appends(chunks: list[str]) -> str:
    """Drive the REAL append_command_output on a minimal AssistantTurn stub.

    query_one('#tools') returns a fake Vertical whose mount() intercepts the
    newly-created Static and replaces _cmd_tail with a _FakeTailStatic so that
    subsequent update() calls are captured without a running Textual App.
    """
    from llamatui.widgets import AssistantTurn
    turn = AssistantTurn.__new__(AssistantTurn)
    turn._cmd_output_lines = []
    turn._cmd_tail = None

    fake_tail = _FakeTailStatic()

    class _FakeTools:
        def mount(self, widget):
            # Replace the real Static with our recorder so update() is captured.
            turn._cmd_tail = fake_tail

    def _query_one(selector, cls=None):
        return _FakeTools()

    turn.query_one = _query_one

    for chunk in chunks:
        turn.append_command_output(chunk)

    from rich.text import Text
    content = fake_tail.content
    if content is None:
        return ""
    return content.plain if isinstance(content, Text) else str(content)


def test_append_command_output_small_stays_intact():
    text = _run_appends(["line1\n", "line2\n", "line3\n"])
    assert text == "line1\nline2\nline3\n"


def test_append_command_output_caps_at_limit():
    # 250 lines → should trim to exactly _CMD_OUTPUT_TAIL_CAP (200) lines
    chunks = [f"line{i}\n" for i in range(250)]
    text = _run_appends(chunks)
    lines = text.splitlines()
    assert len(lines) == _CMD_OUTPUT_TAIL_CAP
    # The tail should contain the LAST 200 lines (50..249)
    assert lines[0] == "line50"
    assert lines[-1] == "line249"


def test_append_command_output_partial_chunk_no_newline():
    # A chunk without a trailing newline is treated as a partial line; no spurious trimming
    text = _run_appends(["hello ", "world"])
    assert text == "hello world"
