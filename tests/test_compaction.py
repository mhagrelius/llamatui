import asyncio

import pytest

from agent_framework import Content, Message

from llamatui.compaction import (
    Compactor,
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


def test_should_compact_threshold():
    c = Compactor()
    cfg = CompactionConfig()
    assert c.should_compact(0.59, cfg) is False
    assert c.should_compact(0.60, cfg) is True
    assert c.should_compact(0.95, cfg) is True


def test_should_compact_only_checks_threshold():
    # should_compact itself only checks the trigger; `enabled` is gated by the caller.
    c = Compactor()
    assert c.should_compact(0.10, CompactionConfig()) is False


@pytest.mark.asyncio
async def test_level1_strips_old_images_not_recent_not_first():
    cfg = CompactionConfig(keep_recent_turns=2)  # window = last 4 msgs
    msgs = [
        _image_user("first"),     # 0 first — image preserved (until floor)
        _assistant("a0"),
        _image_user("middle"),    # 2 old — image stripped
        _assistant("a1"),
        _image_user("recent1"),   # 4 recent — preserved
        _assistant("a2"),
        _image_user("recent2"),   # 6 recent — preserved
        _assistant("a3"),
    ]
    out, res = await Compactor().compact(msgs, 0.65, cfg)
    assert len(out) == len(msgs)                       # Level 1 keeps count
    assert res.removed_images == 1                     # only the "middle" image
    assert any(_is_image_content(c) for c in out[0].contents)   # first kept its image
    assert not any(_is_image_content(c) for c in out[2].contents)  # middle stripped
    assert _extract_text(out[2]) == "middle"
    assert any(getattr(c, "type", None) == "text" and "[image removed]" in (getattr(c, "text", "") or "")
               for c in out[2].contents)
    assert any(_is_image_content(c) for c in out[4].contents)   # recent kept


@pytest.mark.asyncio
async def test_level1_idempotent_and_no_op_when_small():
    cfg = CompactionConfig(keep_recent_turns=5)
    small = [_image_user("only"), _assistant("a")]
    out, res = await Compactor().compact(small, 0.65, cfg)
    assert out == small and res.changed() is False      # nothing old to compact
    big = [_image_user("first"), _assistant("a0")] + \
          [_image_user(f"u{i}") for i in range(3)] + [_assistant("x")] * 9
    once, _ = await Compactor().compact(big, 0.65, cfg)
    twice, res2 = await Compactor().compact(once, 0.65, cfg)
    assert twice == once and res2.removed_images == 0   # idempotent


def _long_history(n_old_turns, keep=2):
    msgs = [_user("FIRST QUESTION")]
    # first turn's answer
    msgs.append(_assistant("first answer"))
    for i in range(n_old_turns):
        msgs.append(_user(f"old question {i}"))
        msgs.append(_assistant(f"old answer {i}"))
    # recent window: `keep` turns
    for j in range(keep):
        msgs.append(_user(f"recent q {j}"))
        msgs.append(_assistant(f"recent a {j}"))
    return msgs


@pytest.mark.asyncio
async def test_level2_heuristic_folds_into_single_summary():
    cfg = CompactionConfig(keep_recent_turns=2, use_llm_summary=False)
    msgs = _long_history(4, keep=2)              # 1 first turn + 4 old + 2 recent
    out, res = await Compactor().compact(msgs, 0.80, cfg)   # >= summarize_threshold
    assert out[0] is msgs[0] or _extract_text(out[0]) == "FIRST QUESTION"
    assert _is_compacted(out[1])                  # the rolling summary
    summary_text = _extract_text(out[1])
    assert "old question 0" in summary_text and "old answer 3" in summary_text
    assert "first answer" in summary_text          # leading orphan answer retained, not dropped
    assert res.summarized_turns == 4
    # recent window intact at the tail
    assert _extract_text(out[-1]) == "recent a 1"
    assert _extract_text(out[-2]) == "recent q 1"


@pytest.mark.asyncio
async def test_level2_rolls_existing_summary_forward():
    cfg = CompactionConfig(keep_recent_turns=2, use_llm_summary=False)
    out1, _ = await Compactor().compact(_long_history(4, keep=2), 0.80, cfg)
    # Append two more turns so one more old turn ages past the window, recompact:
    rolled = list(out1) + [_user("newer q"), _assistant("newer a")]
    out2, res2 = await Compactor().compact(rolled, 0.80, cfg)
    # still exactly one summary artifact right after the first user msg
    assert _is_compacted(out2[1])
    assert sum(1 for m in out2 if _is_compacted(m) and m.role == "assistant") == 1
    assert "old question 0" in _extract_text(out2[1])      # old content retained





@pytest.mark.asyncio
async def test_level2_llm_path_invokes_summarizer():
    seen = {}

    async def fake(msgs):
        seen["n"] = len(msgs)
        return "LLM ROLLING SUMMARY"

    cfg = CompactionConfig(keep_recent_turns=2, use_llm_summary=True)
    out, res = await Compactor(fake).compact(_long_history(4, keep=2), 0.80, cfg)
    assert _extract_text(out[1]) == "LLM ROLLING SUMMARY"
    assert _is_compacted(out[1])
    assert res.summarized_turns == 4
    assert seen["n"] >= 8                        # the aged region was passed


@pytest.mark.asyncio
async def test_level2_llm_falls_back_on_empty():
    async def empty(msgs):
        return ""

    cfg = CompactionConfig(keep_recent_turns=2, use_llm_summary=True)
    out, res = await Compactor(empty).compact(_long_history(3, keep=2), 0.80, cfg)
    assert "old question 0" in _extract_text(out[1])   # heuristic content present
    assert res.summarized_turns == 3


@pytest.mark.asyncio
async def test_level2_llm_falls_back_on_exception():
    async def boom(msgs):
        raise RuntimeError("model down")

    cfg = CompactionConfig(keep_recent_turns=2, use_llm_summary=True)
    out, res = await Compactor(boom).compact(_long_history(3, keep=2), 0.80, cfg)
    assert res.summarized_turns == 3
    assert "old answer 2" in _extract_text(out[1])


@pytest.mark.asyncio
async def test_level2_llm_falls_back_on_timeout():
    async def slow(msgs):
        await asyncio.sleep(0.05)
        return "TOO LATE"

    cfg = CompactionConfig(keep_recent_turns=2, use_llm_summary=True, summary_timeout_s=0.01)
    out, res = await Compactor(slow).compact(_long_history(3, keep=2), 0.80, cfg)
    # summarizer timed out → heuristic fallback, NOT the slow summarizer's output
    assert "TOO LATE" not in _extract_text(out[1])
    assert "old question 0" in _extract_text(out[1])
    assert res.summarized_turns == 3


@pytest.mark.asyncio
async def test_compact_normal_folds_regardless_of_frac():
    cfg = CompactionConfig(keep_recent_turns=2, use_llm_summary=False)
    out, res = await Compactor().compact_normal(_long_history(4, keep=2), cfg)
    assert _is_compacted(out[1]) and res.summarized_turns == 4
    assert _extract_text(out[-1]) == "recent a 1"   # recent window preserved


@pytest.mark.asyncio
async def test_compact_to_floor_strips_all_images_and_reaches_floor():
    cfg = CompactionConfig(keep_recent_turns=2, use_llm_summary=False)
    msgs = [_image_user("FIRST")]                    # first user msg WITH image
    for i in range(4):
        msgs += [_image_user(f"q{i}"), _assistant(f"a{i}")]
    msgs.append(_image_user("current question"))     # trailing lone user (overflowed turn)
    out, res = await Compactor().compact_to_floor(msgs, cfg)
    assert _extract_text(out[0]) == "FIRST"
    assert not any(_is_image_content(c) for m in out for c in m.contents)  # all images gone
    assert _extract_text(out[-1]) == "current question"   # current user preserved
    assert len(out) < len(msgs) and res.changed()
    assert res.removed_images >= 5


@pytest.mark.asyncio
async def test_first_user_text_and_last_user_never_dropped_to_floor():
    cfg = CompactionConfig(keep_recent_turns=5, use_llm_summary=False)
    msgs = [_user("GROUND TRUTH")]
    for i in range(20):
        msgs += [_user(f"q{i}"), _assistant(f"a{i}")]
    out, _ = await Compactor().compact_to_floor(msgs, cfg)
    assert _extract_text(out[0]) == "GROUND TRUTH"
    assert out[-1].role == "user"
