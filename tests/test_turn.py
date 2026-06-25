"""TurnStream is the test surface for how a streamed turn is interpreted.

No App, no server: we feed recorded-shaped update objects and assert the resulting state.
"""

from types import SimpleNamespace

from llamatui.turn import (
    SEARCHING,
    THINKING,
    WRITING,
    TurnStream,
    extract_query,
    strip_tool_noise,
)


class FakeClock:
    """Deterministic monotonic clock: each call advances by ``step``."""

    def __init__(self, start: float = 0.0, step: float = 1.0) -> None:
        self.t = start
        self.step = step

    def __call__(self) -> float:
        v = self.t
        self.t += self.step
        return v


def content(**kw):
    return SimpleNamespace(**kw)


def update(*contents):
    return SimpleNamespace(contents=list(contents))


def test_reasoning_and_answer_are_split():
    s = TurnStream()
    s.ingest(update(content(type="text_reasoning", text="let me ")))
    s.ingest(update(content(type="text_reasoning", text="think")))
    s.ingest(update(content(type="text", text="The ")))
    s.ingest(update(content(type="text", text="answer.")))

    assert s.state.reasoning == "let me think"
    assert s.state.answer == "The answer."
    assert s.state.has_reasoning and s.state.has_answer


def test_phase_moves_thinking_to_writing():
    s = TurnStream()
    assert s.state.phase == THINKING
    s.ingest(update(content(type="text_reasoning", text="hmm")))
    assert s.state.phase == THINKING
    s.ingest(update(content(type="text", text="hi")))
    assert s.state.phase == WRITING


def test_ttft_is_first_token_of_any_kind():
    clock = FakeClock()  # t0 = 0.0; next call = 1.0
    s = TurnStream(clock=clock)
    s.ingest(update(content(type="text_reasoning", text="x")))
    assert s.state.ttft_s == 1.0
    # a later token does not move ttft
    s.ingest(update(content(type="text", text="y")))
    assert s.state.ttft_s == 1.0


def test_tool_call_accumulates_args_and_completes():
    s = TurnStream()
    s.ingest(update(content(type="function_call", name="exa", call_id="c1", arguments='{"que')))
    assert s.state.phase == SEARCHING
    s.ingest(update(content(type="function_call", call_id="c1", arguments='ry":"python 3.14"}')))
    s.ingest(update(content(type="function_result", call_id="c1")))

    assert len(s.state.tool_calls) == 1
    call = s.state.tool_calls[0]
    assert call.name == "exa"
    assert call.args == '{"query":"python 3.14"}'
    assert call.query == "python 3.14"
    assert call.done is True


def test_usage_reads_details_and_timings_off_raw_chunk():
    s = TurnStream()
    raw = SimpleNamespace(timings={"predicted_per_second": 80.0})
    s.ingest(update(content(
        type="usage",
        usage_details={"total_token_count": 100},
        raw_representation=raw,
    )))
    assert s.state.usage_details == {"total_token_count": 100}
    assert s.state.timings == {"predicted_per_second": 80.0}


def test_usage_falls_back_to_model_extra_for_timings():
    s = TurnStream()
    raw = SimpleNamespace(model_extra={"timings": {"prompt_per_second": 2600.0}})
    s.ingest(update(content(type="usage", usage_details={}, raw_representation=raw)))
    assert s.state.timings == {"prompt_per_second": 2600.0}


def test_function_result_captures_text_and_failure():
    s = TurnStream()
    s.ingest(update(content(type="function_call", name="remember", call_id="c1", arguments='{"content":"x"}')))
    s.ingest(update(content(type="function_result", call_id="c1", result="Noted about user.")))
    call = s.state.tool_calls[0]
    assert call.done and call.result == "Noted about user." and call.failed is False

    s2 = TurnStream()
    s2.ingest(update(content(type="function_call", name="remember", call_id="c2", arguments="{}")))
    s2.ingest(update(content(type="function_result", call_id="c2", exception="missing 'content'")))
    bad = s2.state.tool_calls[0]
    assert bad.done and bad.failed and "missing" in bad.result


def test_strip_tool_noise_removes_leaked_calls():
    # A model leaked a whole tool call into the answer text; it should vanish.
    leaked = (
        "Sure, saving that now.\n"
        "<tool_call> <function=remember> <parameter=content> Favorite games: Elden Ring "
        "<parameter=subject> Matt </tool_call>"
    )
    assert strip_tool_noise(leaked) == "Sure, saving that now."
    # Stray opening tags without a close still get stripped.
    assert "<function=" not in strip_tool_noise("hi <function=remember> there")
    # Plain prose is untouched (and fast-pathed).
    assert strip_tool_noise("no markup at all") == "no markup at all"


def test_extract_query_tolerates_partial_json():
    assert extract_query('{"query":"hel') is None  # value not yet closed
    assert extract_query('{"query":"hello"}') == "hello"
    assert extract_query("") is None
    assert extract_query('{"n":1}') is None


def _usage_update(out, reason):
    c = SimpleNamespace(
        type="usage",
        usage_details={"output_token_count": out, "input_token_count": 100,
                       "total_token_count": 100 + out, "reasoning_output_token_count": reason},
        raw_representation=None,
    )
    return SimpleNamespace(contents=[c])


def test_usage_sums_across_segments_keeps_last_context():
    s = TurnStream()
    s.ingest(_usage_update(10, 3))
    s.ingest(_usage_update(20, 5))
    u = s.state.usage_details
    assert u["output_token_count"] == 30          # summed generated tokens
    assert u["reasoning_output_token_count"] == 8  # summed reasoning
    assert u["input_token_count"] == 100           # last segment's prompt
    assert u["total_token_count"] == 120           # last segment's total (cumulative context)
