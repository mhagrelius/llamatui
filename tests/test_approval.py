"""Tests for the approval modal's pure pieces.

The live approve/deny loop in app.generate() needs a real llama-server driving a tool call, so
it is verified manually (see task-3 brief Step 4). Here we cover what is pure: the one-line
``_describe`` rendering for each tool shape, and the request→response mapping that the worker
relies on (built against real agent_framework Content so the spike's API contract is exercised).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent_framework import Content

from llamatui.approval import ApprovalModal, _describe


def _call(name, **args):
    """A stand-in function_call content: _describe only reads .name and .arguments."""
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


# ---- _render_call for run_command shows cwd + shell (spec §H) -------------

class _FakeWorkspace:
    """Minimal workspace stub: workspace_line() returns a predictable string."""
    def __init__(self, root, shell="sh"):
        self.root = root
        self._shell_name = shell

    def workspace_line(self):
        return f"Workspace: {self.root} · shell: {self._shell_name}"


def test_render_call_run_command_shows_cwd_and_shell(tmp_path):
    """_render_call for run_command must include the workspace root and shell."""
    ws = _FakeWorkspace(root=str(tmp_path), shell="PowerShell")
    modal = ApprovalModal.__new__(ApprovalModal)
    modal._workspace = ws
    call = _call("run_command", command="pytest -q")
    rendered = modal._render_call(call)
    assert str(tmp_path) in rendered
    assert "PowerShell" in rendered
    assert "pytest -q" in rendered


def test_render_call_run_command_no_workspace_falls_back():
    """_render_call for run_command without workspace falls back gracefully."""
    modal = ApprovalModal.__new__(ApprovalModal)
    modal._workspace = None
    call = _call("run_command", command="ls -la")
    rendered = modal._render_call(call)
    assert "run_command" in rendered
    assert "ls -la" in rendered


# ---- Fix 1: allow_approve_all gate on ApprovalModal ----------------------

def test_approval_modal_allow_approve_all_false_flag():
    """ApprovalModal with allow_approve_all=False stores the flag correctly."""
    reqs = [_request("r1", "run_command")]
    modal = ApprovalModal(reqs, allow_approve_all=False)
    assert modal._allow_approve_all is False


def test_approval_modal_allow_approve_all_true_flag():
    """ApprovalModal with allow_approve_all=True stores the flag correctly."""
    reqs = [_request("r1", "write_file")]
    modal = ApprovalModal(reqs, allow_approve_all=True)
    assert modal._allow_approve_all is True


def test_approval_modal_default_allow_approve_all_is_false():
    """ApprovalModal defaults allow_approve_all to False (backward-compat)."""
    reqs = [_request("r1", "write_file")]
    modal = ApprovalModal(reqs)
    assert modal._allow_approve_all is False


# ---- Fix 1 + Fix 3: _resolve_approvals logic — has_typed determines flag ----

def _make_request_simple(rid: str, name: str):
    from types import SimpleNamespace
    fc = SimpleNamespace(name=name)
    return SimpleNamespace(id=rid, function_call=fc)


def test_resolve_approvals_run_command_only_has_typed_false():
    """A batch of only run_command requests → has_typed is False (no blanket-approve button)."""
    from llamatui.filesystem import ALWAYS_PROMPT_TOOLS
    requests = [_make_request_simple("r1", "run_command")]
    always_prompt = [r for r in requests if getattr(r.function_call, "name", "") in ALWAYS_PROMPT_TOOLS]
    typed = [r for r in requests if r not in always_prompt]
    has_typed = bool(typed)
    assert has_typed is False


def test_resolve_approvals_mixed_batch_has_typed_true():
    """A batch with write_file + run_command → has_typed is True."""
    from llamatui.filesystem import ALWAYS_PROMPT_TOOLS
    requests = [
        _make_request_simple("r1", "write_file"),
        _make_request_simple("r2", "run_command"),
    ]
    always_prompt = [r for r in requests if getattr(r.function_call, "name", "") in ALWAYS_PROMPT_TOOLS]
    typed = [r for r in requests if r not in always_prompt]
    has_typed = bool(typed)
    assert has_typed is True


def test_always_prompt_tools_contains_run_command():
    """ALWAYS_PROMPT_TOOLS must contain run_command and be a frozenset."""
    from llamatui.filesystem import ALWAYS_PROMPT_TOOLS
    assert "run_command" in ALWAYS_PROMPT_TOOLS
    assert isinstance(ALWAYS_PROMPT_TOOLS, frozenset)
