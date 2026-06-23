"""llamatui — a Textual TUI over a local llama-server, built on the Agent Framework.

Features: streamed answers with a distinct thinking pane, live throughput metrics, Exa
web-search (remote MCP) the model can call on its own, and elia-style persisted
conversations you can switch between from a sidebar.

The App is deliberately thin. Two deep modules carry the load behind narrow interfaces:
:class:`~llamatui.turn.TurnStream` folds the streamed turn into structured state, and
:class:`~llamatui.conversation.Conversation` owns the agent-facing history together with its
persistence. The streaming worker here is just an *adapter* that pumps the accumulator and
reflects its state into widgets.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from . import metrics as M
from .client import build_agent, detect_context_window, detect_model_id
from .conversation import Conversation
from .graph import KnowledgeGraph, build_embedder
from .instructions import build_instructions
from .memory import Memory
from .storage import Store, connect
from .tools import build_exa_tool, exa_key_present
from .turn import WRITING, TurnStream
from .widgets import AssistantTurn, PromptArea, StatusBar, UserTurn

HELP = """[b]commands[/b]
  [cyan]/new[/]              start a new conversation (also Ctrl+N)
  [cyan]/system <text>[/]    set or replace the system prompt
  [cyan]/think[/]            toggle whether thinking panes are shown
  [cyan]/help[/]             this list
  [cyan]/exit[/], [cyan]/quit[/]      leave"""

DEFAULT_SYSTEM = (
    "You are a helpful personal AI assistant running locally on the user's own machine via "
    "llama.cpp. You serve a single user — your principal — with whatever they need: answering "
    "questions, thinking through problems, drafting and editing writing, writing and explaining "
    "code, planning, and looking things up.\n\n"
    "Be direct and concise by default, and expand only when the task genuinely needs depth. Lead "
    "with the answer, then add reasoning or caveats if they're useful. Use Markdown for structure "
    "and code blocks. When you're unsure, say so plainly instead of guessing, and never fabricate "
    "facts, quotes, citations, or URLs. Ask a brief clarifying question only when the request is "
    "genuinely ambiguous; otherwise make a sensible assumption, state it, and proceed. This is a "
    "private, local setup, so respect the user's time and privacy."
)

WEB_SEARCH_GUIDANCE = (
    "You have a web search tool powered by Exa. Your training data has a cutoff, so use it "
    "whenever the answer depends on current, recent, or fast-changing information — news, prices, "
    "releases and version numbers, dates, schedules, people, ongoing events — or when the user "
    "asks you to look something up, references a specific URL or document, or when you're not "
    "confident a fact is still current. Prefer searching over guessing on anything time-sensitive. "
    "Use focused queries, corroborate important claims across more than one source, and cite the "
    "source URLs you relied on. For stable general knowledge or your own reasoning, answer "
    "directly without searching."
)

MEMORY_GUIDANCE = (
    "You have a persistent memory that survives across conversations. Use the 'remember' tool to "
    "save durable facts about the user and their world as they emerge — preferences, ongoing "
    "projects, people, decisions, and how things relate (pass related_to/relation to link them). "
    "Use 'recall' to look something up before answering questions about the user when it isn't "
    "already in the summary below, and 'forget' to remove things they ask you to drop. Save only "
    "lasting facts, not throwaway details, and don't re-save something already shown below. A "
    "summary of what you currently know is included as context."
)


def _date_line() -> str:
    # Kept last in the system prompt: it's the only volatile part, so the stable instruction
    # prefix above it stays identical day to day and remains cacheable by llama-server.
    now = datetime.now().astimezone()
    return f"Current date: {now:%A, %Y-%m-%d}. Treat this as today when reasoning about time."


RENDER_INTERVAL = 0.06


class Config:
    def __init__(
        self, url, model, system, temperature, max_tokens, top_p,
        db_path=None, web=True, memory=True,
    ):
        self.url = url
        self.model = model
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.db_path = db_path
        self.web = web
        self.memory = memory


class LlamaTUI(App):
    CSS_PATH = "styles.tcss"
    TITLE = "llamatui"

    BINDINGS = [
        Binding("ctrl+n", "new_chat", "New"),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+t", "toggle_thinking", "Thinking"),
        Binding("ctrl+d", "delete_chat", "Delete"),
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.show_thinking = True
        self.model_label = config.model
        self.context_window: int | None = None
        self.agent = None
        self._busy = False
        self.store: Store | None = None
        self.conversation: Conversation | None = None
        self.web_tool = None
        self.web_enabled = False
        self.memory: Memory | None = None

    # ---- setup -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("  Conversations", id="sidebar-title")
                yield ListView(id="conv-list")
            with Vertical(id="main"):
                yield VerticalScroll(id="transcript")
                yield StatusBar(id="status")
                yield PromptArea(id="prompt", soft_wrap=True)
        yield Footer()

    async def on_mount(self) -> None:
        # One shared connection: Store owns conversations, KnowledgeGraph owns the graph.
        conn = connect(self.config.db_path)
        self.store = Store(conn)
        detected = detect_model_id(self.config.url)
        if detected:
            self.config.model = detected
            self.model_label = detected.replace("\\", "/").rsplit("/", 1)[-1]
        self.context_window = detect_context_window(self.config.url)
        connected = detected is not None

        self.conversation = Conversation(self.store, model=self.model_label)
        self.conversation.system_prompt = self.config.system

        if self.config.web:
            self.web_tool = build_exa_tool()
            try:
                await asyncio.wait_for(self.web_tool.connect(), timeout=12)
                self.web_enabled = True
            except Exception:
                self.web_enabled = False

        if self.config.memory:
            # Keyword recall + the ambient block work immediately; semantic recall upgrades in
            # place once the (optional) embedding model finishes loading off the event loop.
            self.memory = Memory(KnowledgeGraph(conn))
            self._load_embedder()

        self._rebuild_agent()
        self._refresh_sidebar()
        self.query_one("#prompt", PromptArea).focus()

        web = (
            f"web search [b]on[/]{'' if exa_key_present() else ' (keyless)'}"
            if self.web_enabled
            else "web search [b]off[/]"
        )
        mem = f"memory [b]{'on' if self.memory is not None else 'off'}[/]"
        self._write_system(
            f"Connected to [b]{self.config.url}[/]  ·  model [b]{self.model_label}[/]"
            + (f"  ·  ctx {self.context_window:,}" if self.context_window else "")
            + f"  ·  {web}  ·  {mem}"
            + "\nType a message, or [cyan]/help[/] for commands."
        )
        self._status("ready", connected=connected)

    async def on_unmount(self) -> None:
        if self.web_tool is not None:
            try:
                await self.web_tool.close()
            except Exception:
                pass
        if self.store is not None:
            self.store.close()

    @work(thread=True, group="embedder")
    def _load_embedder(self) -> None:
        # Build the (optional, slow-to-load) embedder OFF the event loop — but it must not touch
        # SQLite here: the connection lives on the main thread. So we only load, then hand it back.
        embedder = build_embedder()
        if embedder is not None:
            self.call_from_thread(self._attach_embedder, embedder)

    def _attach_embedder(self, embedder) -> None:
        # Runs on the main thread (writes SQLite during backfill). One public seam, no internals.
        if self.memory is not None:
            self.memory.attach_embedder(embedder)

    def _rebuild_agent(self) -> None:
        capabilities: list[str] = []
        tools: list = []
        if self.web_enabled:
            capabilities.append(WEB_SEARCH_GUIDANCE)
            tools.append(self.web_tool)
        ambient = None
        if self.memory is not None:
            capabilities.append(MEMORY_GUIDANCE)
            tools.extend(self.memory.build_tools())
            ambient = self.memory.preamble()
        # The builder guarantees the volatile date line lands last (cache-prefix invariant).
        instructions = build_instructions(
            persona=self.conversation.system_prompt or DEFAULT_SYSTEM,
            capabilities=capabilities,
            ambient=ambient,
            volatile=_date_line(),
        )
        self.agent = build_agent(
            base_url=self.config.url,
            model=self.config.model,
            instructions=instructions or None,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            top_p=self.config.top_p,
            tools=tools or None,
        )

    # ---- helpers ---------------------------------------------------------
    @property
    def transcript(self) -> VerticalScroll:
        return self.query_one("#transcript", VerticalScroll)

    def _status(self, state: str, detail: str = "", connected: bool = True) -> None:
        self.query_one("#status", StatusBar).show(
            model=self.model_label, state=state, detail=detail, connected=connected
        )

    def _write_system(self, markup: str) -> None:
        self.transcript.mount(Static(markup, classes="system-note"))
        self.transcript.scroll_end(animate=False)

    def _refresh_sidebar(self) -> None:
        if self.store is None:
            return
        lv = self.query_one("#conv-list", ListView)
        lv.clear()
        active_id = self.conversation.id if self.conversation else None
        for row in self.store.list_conversations():
            item = ListItem(Label(row["title"] or "Untitled"))
            item.conv_id = row["id"]  # type: ignore[attr-defined]
            if row["id"] == active_id:
                item.add_class("-active")
            lv.append(item)

    # ---- input handling --------------------------------------------------
    async def on_prompt_area_submitted(self, event: PromptArea.Submitted) -> None:
        text = event.value.strip()
        prompt = self.query_one("#prompt", PromptArea)
        if not text or self._busy:
            return
        prompt.clear()
        if text.startswith("/"):
            self._handle_command(text)
            return
        await self._send(text)

    def _handle_command(self, text: str) -> None:
        cmd, _, rest = text.partition(" ")
        rest = rest.strip()
        if cmd in ("/exit", "/quit"):
            self.exit()
        elif cmd == "/help":
            self._write_system(HELP)
        elif cmd in ("/new", "/reset"):
            self.action_new_chat()
        elif cmd == "/think":
            self.action_toggle_thinking()
        elif cmd == "/system":
            self.conversation.system_prompt = rest or None
            self._rebuild_agent()
            self._write_system(f"[dim](system prompt {'updated' if rest else 'cleared'})[/]")
        else:
            self._write_system(f"[red]unknown command:[/] {cmd}  —  try [cyan]/help[/]")

    async def _send(self, text: str) -> None:
        await self.transcript.mount(UserTurn(text))
        self.conversation.append_user(text)
        turn = AssistantTurn(show_thinking=self.show_thinking)
        await self.transcript.mount(turn)
        self.transcript.scroll_end(animate=False)
        self.generate(turn, text)

    # ---- streaming worker (adapter over TurnStream) ----------------------
    @work(exclusive=True, group="gen")
    async def generate(self, turn: AssistantTurn, user_text: str) -> None:
        self._busy = True
        stream = TurnStream()
        last_render = 0.0
        collapsed = False
        seen_calls: set[str] = set()

        def reflect_tools() -> None:
            for call in stream.state.tool_calls:
                label = call.name + (f"  «{call.query}»" if call.query else "")
                if call.call_id not in seen_calls:
                    seen_calls.add(call.call_id)
                    turn.add_tool_call(call.call_id, call.name)
                if call.done:
                    turn.update_tool(call.call_id, label + "  · done", done=True)
                else:
                    turn.update_tool(call.call_id, label)

        def render(force: bool = False) -> None:
            nonlocal last_render
            now = time.monotonic()
            if not force and now - last_render < RENDER_INTERVAL:
                return
            last_render = now
            st = stream.state
            if st.reasoning:
                turn.set_reasoning(st.reasoning)
            if st.answer:
                turn.set_answer(st.answer)
            reflect_tools()
            chars = len(st.answer) if st.answer else len(st.reasoning)
            rate = (chars // 4) / max(1e-6, stream.elapsed() - (st.ttft_s or 0.0))
            self._status(st.phase + "…", detail=f"~{rate:.0f} tok/s", connected=True)
            self.transcript.scroll_end(animate=False)

        try:
            async for update in self.agent.run(self.conversation.messages_for_agent(), stream=True):
                stream.ingest(update)
                if not collapsed and stream.state.phase == WRITING:
                    turn.collapse_thinking()
                    collapsed = True
                render()
        except Exception as exc:
            render(force=True)
            turn.set_metrics(f"⚠ {type(exc).__name__}: {exc}", classes="error")
            self._status("error", connected=False)
            self.conversation.undo_last_user()
            self._busy = False
            return

        render(force=True)
        st = stream.state
        elapsed = stream.elapsed()

        if not st.has_reasoning:
            turn.drop_thinking()
        else:
            rt = (st.usage_details or {}).get("reasoning_output_token_count")
            turn.set_think_title(f"Thinking ({rt:,} tokens)" if rt else "Thinking")
            turn.collapse_thinking()

        m = M.extract(
            st.usage_details, st.timings, ttft_s=st.ttft_s, elapsed_s=elapsed,
            answer_chars=len(st.answer), context_window=self.context_window,
        )
        metrics_line = M.format_oneline(m)
        turn.set_metrics(metrics_line)

        # persist the completed exchange (history + storage together)
        self.conversation.append_assistant(
            user_text=user_text,
            answer=st.answer,
            reasoning=st.reasoning or None,
            metrics={"line": metrics_line},
        )
        self._refresh_sidebar()

        ctx = ""
        if m.context_frac is not None:
            ctx = f"ctx {m.context_used:,}/{m.context_window:,} ({m.context_frac*100:.0f}%)"
        self._status("ready", detail=ctx, connected=True)
        self._busy = False

    # ---- conversation management ----------------------------------------
    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        cid = getattr(event.item, "conv_id", None)
        if cid is not None and self.conversation and cid != self.conversation.id:
            await self.open_conversation(cid)

    async def open_conversation(self, conv_id: int) -> None:
        if self.conversation is None:
            return
        self.workers.cancel_group(self, "gen")
        self._busy = False
        rows = self.conversation.load(conv_id)
        if rows is None:
            return
        self._rebuild_agent()
        for child in list(self.transcript.children):
            await child.remove()
        for row in rows:
            if row["role"] == "user":
                await self.transcript.mount(UserTurn(row["content"]))
            else:
                turn = AssistantTurn(show_thinking=self.show_thinking)
                await self.transcript.mount(turn)
                line = None
                if row["metrics"]:
                    try:
                        line = json.loads(row["metrics"]).get("line")
                    except Exception:
                        line = None
                turn.load_saved(answer=row["content"], reasoning=row["reasoning"], metrics_line=line)
        self.transcript.scroll_end(animate=False)
        self._refresh_sidebar()
        self._status("ready", detail=f"“{self.conversation.title}”", connected=True)
        self.query_one("#prompt", PromptArea).focus()

    def action_new_chat(self) -> None:
        self.workers.cancel_group(self, "gen")
        self._busy = False
        if self.conversation is not None:
            self.conversation.new(self.conversation.system_prompt)
        self._rebuild_agent()  # refresh the ambient memory block for the fresh conversation
        for child in list(self.transcript.children):
            child.remove()
        self._refresh_sidebar()
        self._write_system("[dim](new conversation)[/]")
        self.query_one("#prompt", PromptArea).focus()

    def action_delete_chat(self) -> None:
        if self.store is None or self.conversation is None:
            return
        lv = self.query_one("#conv-list", ListView)
        item = lv.highlighted_child
        cid = getattr(item, "conv_id", None) if item else None
        if cid is None:
            return
        self.store.delete_conversation(cid)
        if cid == self.conversation.id:
            self.action_new_chat()
        else:
            self._refresh_sidebar()

    # ---- view actions ----------------------------------------------------
    def action_cancel(self) -> None:
        if self._busy:
            self.workers.cancel_group(self, "gen")
            self._busy = False
            self._status("cancelled", connected=True)
            if self.conversation is not None:
                self.conversation.undo_last_user()

    def action_toggle_thinking(self) -> None:
        self.show_thinking = not self.show_thinking
        for turn in self.query(AssistantTurn):
            turn.set_thinking_visible(self.show_thinking)
        self._write_system(
            f"[dim](thinking panes {'shown' if self.show_thinking else 'hidden'})[/]"
        )

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display
