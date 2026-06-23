"""Custom Textual widgets for the chat UI."""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Collapsible, Markdown, Static, TextArea


class PromptArea(TextArea):
    """A multi-line prompt. Enter submits; Ctrl+J inserts a newline."""

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.text))
            return
        if event.key == "ctrl+j":
            event.prevent_default()
            event.stop()
            self.insert("\n")
            return
        super()._on_key(event)


class UserTurn(Static):
    """A user message bubble."""

    def __init__(self, text: str) -> None:
        super().__init__(Text(text, no_wrap=False), classes="user-turn")


class AssistantTurn(Vertical):
    """One assistant reply: a collapsible thinking pane, the answer, and metrics."""

    def __init__(self, show_thinking: bool = True) -> None:
        super().__init__(classes="assistant-turn")
        self._reasoning = ""
        self._answer = ""
        self._show_thinking = show_thinking

    def compose(self) -> ComposeResult:
        with Collapsible(title="Thinking…", collapsed=False, id="think"):
            yield Markdown("", id="think-body")
        yield Vertical(id="tools")
        yield Markdown("", id="answer")
        yield Static("", id="turn-metrics")

    def on_mount(self) -> None:
        if not self._show_thinking:
            self.query_one("#think").display = False

    # ---- tool calls ------------------------------------------------------
    def add_tool_call(self, call_id: str, name: str) -> None:
        line = Static(Text(f"🔎 {name} …", style="cyan"), classes="tool-call")
        line.tool_call_id = call_id  # type: ignore[attr-defined]
        self.query_one("#tools", Vertical).mount(line)

    def update_tool(self, call_id: str, label: str, done: bool = False, failed: bool = False) -> None:
        for line in self.query(".tool-call"):
            if getattr(line, "tool_call_id", None) == call_id:
                if failed:
                    mark, style = "⚠", "yellow"
                elif done:
                    mark, style = "✓", "green"
                else:
                    mark, style = "🔎", "cyan"
                line.update(Text(f"{mark} {label}", style=style))
                return

    # ---- streaming feeds -------------------------------------------------
    def set_reasoning(self, text: str) -> None:
        self._reasoning = text
        self.query_one("#think-body", Markdown).update(text)

    def set_answer(self, text: str) -> None:
        self._answer = text
        self.query_one("#answer", Markdown).update(text)

    @property
    def has_reasoning(self) -> bool:
        return bool(self._reasoning.strip())

    # ---- lifecycle -------------------------------------------------------
    def set_think_title(self, title: str) -> None:
        self.query_one("#think", Collapsible).title = title

    def collapse_thinking(self) -> None:
        try:
            self.query_one("#think", Collapsible).collapsed = True
        except Exception:
            pass

    def drop_thinking(self) -> None:
        """Remove the thinking pane entirely (model produced none)."""
        try:
            self.query_one("#think").display = False
        except Exception:
            pass

    def set_thinking_visible(self, visible: bool) -> None:
        if self.has_reasoning:
            self.query_one("#think").display = visible

    def set_metrics(self, line: str, classes: str = "") -> None:
        widget = self.query_one("#turn-metrics", Static)
        widget.update(Text(line, style="dim"))
        if classes:
            widget.set_classes(f"turn-metrics {classes}")

    def load_saved(self, *, answer: str, reasoning: str | None, metrics_line: str | None) -> None:
        """Populate a turn from persisted data (no streaming)."""
        if reasoning:
            self.set_reasoning(reasoning)
            self.set_think_title("Thinking")
            self.collapse_thinking()
        else:
            self.drop_thinking()
        self.set_answer(answer)
        if metrics_line:
            self.set_metrics(metrics_line)


class StatusBar(Static):
    """A single live line above the prompt: model, state, running throughput."""

    def show(self, *, model: str, state: str, detail: str = "", connected: bool = True) -> None:
        dot = "●" if connected else "○"
        text = Text()
        text.append(f" {dot} ", style="green" if connected else "red")
        text.append(model, style="bold")
        text.append("   ")
        text.append(state, style="cyan")
        if detail:
            text.append("   ")
            text.append(detail, style="dim")
        self.update(text)
