"""TurnStream is the test surface for how a streamed turn is interpreted.

No App, no server: we feed recorded-shaped update objects and assert the resulting state.
"""

from types import SimpleNamespace

from llamatui.turn import SEARCHING, THINKING, WRITING, TurnStream, extract_query


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


def test_extract_query_tolerates_partial_json():
    assert extract_query('{"query":"hel') is None  # value not yet closed
    assert extract_query('{"query":"hello"}') == "hello"
    assert extract_query("") is None
    assert extract_query('{"n":1}') is None
