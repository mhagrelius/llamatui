"""Tests for the approval modal's pure pieces.

The live approve/deny loop in app.generate() needs a real llama-server driving a tool call, so
it is verified manually (see task-3 brief Step 4). Here we cover what is pure: the one-line
``_describe`` rendering for each tool shape, and the request→response mapping that the worker
relies on (built against real agent_framework Content so the spike's API contract is exercised).
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_framework import Content

from llamatui.approval import _describe


def _call(name, **args):
    """A stand-in function_call content: _describe only reads .name and .arguments."""
    import json
    return SimpleNamespace(name=name, arguments=json.dumps(args))


def test_describe_write_file_shows_path():
    assert _describe(_call("write_file", path="notes/todo.md", content="x")) == "write_file: notes/todo.md"


def test_describe_run_command_shows_command():
    assert _describe(_call("run_command", command="ls -la")) == "run_command: ls -la"


def test_describe_move_and_delete_show_path():
    assert _describe(_call("move", path="a.txt")) == "move: a.txt"
    assert _describe(_call("delete", path="b.txt")) == "delete: b.txt"


def test_describe_move_falls_back_to_src():
    assert _describe(_call("move", src="a.txt")) == "move: a.txt"


def test_describe_fallback_for_unknown_tool():
    out = _describe(_call("frobnicate", knob=3))
    assert out.startswith("frobnicate:")
    assert "knob" in out


def test_describe_handles_unparseable_arguments():
    # Non-JSON arguments must not raise — they land in the fallback bucket.
    call = SimpleNamespace(name="write_file", arguments="not json {")
    out = _describe(call)
    assert out == "write_file: "  # path missing from the {'args': ...} fallback dict


def test_describe_handles_missing_attrs():
    assert _describe(SimpleNamespace()).startswith("?:")


# ---- request → approval-response mapping (real framework Content) ---------

def _request(rid: str, name: str):
    fc = Content.from_function_call(call_id=f"call-{rid}", name=name, arguments="{}")
    return Content.from_function_approval_request(id=rid, function_call=fc)


def test_to_function_approval_response_carries_decision():
    req = _request("r1", "write_file")
    approved = req.to_function_approval_response(approved=True)
    denied = req.to_function_approval_response(approved=False)
    assert approved.type == "function_approval_response"
    assert approved.approved is True
    assert approved.id == "r1"
    assert denied.approved is False
    assert denied.id == "r1"
