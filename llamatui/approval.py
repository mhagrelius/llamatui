"""ApprovalModal — the human gate for filesystem actions that mutate or run commands.

Shown by app.generate() when a turn pauses on a function_approval_request. Pure UI: it renders
the pending call(s) and returns the user's per-call decision; the worker turns that into
function_approval_response content and resumes the run.
"""

from __future__ import annotations

import json

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


def _describe(call) -> str:
    """One-line human description of a pending function_call content."""
    name = getattr(call, "name", "?")
    args = getattr(call, "arguments", "") or ""
    try:
        parsed = json.loads(args) if isinstance(args, str) else dict(args)
    except Exception:
        parsed = {"args": args}
    if name == "run_command":
        return f"run_command: {parsed.get('command', '')}"
    if name == "write_file":
        return f"write_file: {parsed.get('path', '')}"
    if name in ("move", "delete"):
        return f"{name}: {parsed.get('path', parsed.get('src', ''))}"
    return f"{name}: {parsed}"


class ApprovalModal(ModalScreen[dict]):
    BINDINGS = [Binding("escape", "deny", "Deny")]

    def __init__(self, requests: list, *, workspace=None) -> None:
        super().__init__()
        self._requests = requests  # list of function_approval_request Content
        self._workspace = workspace  # used for write_file diff previews (Task 15); unused until then

    def _render_call(self, call) -> str:
        """Return the display text for a single pending function_call.

        For write_file, show a diff/preview via workspace.preview_write if a workspace
        is available; otherwise fall back to the one-liner _describe.
        """
        name = getattr(call, "name", "?")
        if name == "write_file" and self._workspace is not None:
            args = getattr(call, "arguments", "") or ""
            try:
                import json
                parsed = json.loads(args) if isinstance(args, str) else dict(args)
            except Exception:
                parsed = {}
            path = parsed.get("path", "")
            content = parsed.get("content", "")
            return self._workspace.preview_write(path, content)
        return _describe(call)

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-box"):
            yield Static("[b]Approve action?[/b]", id="approval-title")
            with VerticalScroll(id="approval-body"):
                for req in self._requests:
                    yield Static(self._render_call(req.function_call), classes="approval-call")
            yield Button("Approve", id="approve", variant="success")
            yield Button("Approve all this turn", id="approve-all", variant="warning")
            yield Button("Deny", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.dismiss({req.id: True for req in self._requests})
        elif event.button.id == "approve-all":
            self.dismiss({req.id: True for req in self._requests} | {"__all__": True})
        else:
            self.action_deny()

    def action_deny(self) -> None:
        self.dismiss({req.id: False for req in self._requests})
