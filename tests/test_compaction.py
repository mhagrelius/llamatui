from agent_framework import Content, Message

from llamatui.compaction import (
    CompactionConfig,
    CompactionResult,
    is_context_overflow,
    overflow_recoverable,
    _is_image_content,
    _extract_text,
    _mark_compacted,
    _is_compacted,
)


def _user(text):
    return Message(role="user", contents=[Content.from_text(text=text)])


def _assistant(text):
    return Message(role="assistant", contents=[Content.from_text(text=text)])


def _image_user(text, data=b"\x89PNG\r\n"):
    return Message(role="user", contents=[
        Content.from_text(text=text),
        Content.from_data(data=data, media_type="image/png"),
    ])


def test_image_content_detected_by_data_type_and_media():
    img = _image_user("see this").contents[1]
    txt = _user("hi").contents[0]
    assert _is_image_content(img) is True
    assert _is_image_content(txt) is False


def test_extract_text_returns_first_text_part():
    assert _extract_text(_image_user("hello")) == "hello"
    assert _extract_text(Message(role="assistant", contents=[])) == ""


def test_marker_round_trips():
    m = _mark_compacted(_assistant("x"))
    assert _is_compacted(m) is True
    assert _is_compacted(_assistant("x")) is False


def test_config_summarize_threshold_is_midpoint():
    assert CompactionConfig().summarize_threshold == (0.60 + 0.85) / 2


def test_result_note_and_changed():
    empty = CompactionResult()
    assert empty.changed() is False
    res = CompactionResult(dropped_messages=4, removed_images=2, summarized_turns=3)
    assert res.changed() is True
    note = res.note()
    assert "2 image" in note and "3" in note


def test_is_context_overflow_detects_keywords_and_cause():
    assert is_context_overflow(Exception("context length exceeded")) is True
    assert is_context_overflow(Exception("the prompt is too long")) is True
    wrapped = RuntimeError("request failed")
    wrapped.__cause__ = ValueError("exceeds the model's maximum context length")
    assert is_context_overflow(wrapped) is True


def test_is_context_overflow_ignores_unrelated():
    assert is_context_overflow(ConnectionError("network down")) is False
    assert is_context_overflow(TimeoutError("read timed out")) is False


def test_overflow_recoverable_safety_properties():
    of = Exception("context length exceeded")
    # fresh overflow, enabled, no approvals → recover
    assert overflow_recoverable(attempts=0, enabled=True, approvals_resolved=False, exc=of) is True
    # ADR-0004: an approval already ran → never recover
    assert overflow_recoverable(attempts=0, enabled=True, approvals_resolved=True, exc=of) is False
    # already retried once → no second attempt
    assert overflow_recoverable(attempts=1, enabled=True, approvals_resolved=False, exc=of) is False
    # compaction disabled → no recovery
    assert overflow_recoverable(attempts=0, enabled=False, approvals_resolved=False, exc=of) is False
    # unrelated error → not an overflow
    assert overflow_recoverable(attempts=0, enabled=True, approvals_resolved=False,
                                exc=ConnectionError("down")) is False
