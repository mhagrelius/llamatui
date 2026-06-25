"""llamatui — a Textual TUI over a local llama-server, built on the Agent Framework.

Features: streamed answers with a distinct thinking pane, live throughput metrics, Exa
web-search (remote MCP) the model can call on its own, and elia-style persisted
conversations you can switch between from a sidebar.

The App is deliberately thin. Deep modules carry the load behind narrow interfaces:
:class:`~llamatui.turn.TurnStream` folds the streamed turn into structured state,
:class:`~llamatui.turn_view.TurnView` folds that state into the assistant-turn widget, and
:class:`~llamatui.conversation.Conversation` owns the agent-facing history together with its
persistence. The streaming worker here just pumps `TurnStream` and hands its state to `TurnView`.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from agent_framework import Message
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static

from . import metrics as M
from . import paths
from .agent_builder import AgentBuilder
from .approval import ApprovalModal
from .client import detect_context_window, detect_model_id, humanize_model_name
from .conversation import Conversation
from .dictation import Dictation, State, build_recorder
from .graph import KnowledgeGraph, build_embedder
from .memory import Memory
from .paths import default_whisper_dir
from .settings import (
    DEFAULTS, SAMPLING_FIELDS, Settings, changed_fields, load as load_settings,
    save_changes,
)
from .settings_screen import SettingsScreen
from .storage import Store, connect
from .tools import build_exa_tool, exa_key_present
from .turn import TurnStream, strip_tool_noise
from .turn_view import TurnView, metrics_blob
from .voice import VoiceInput, keyboard_initial_delay_s
from .whisper import WhisperServer
from .widgets import AssistantTurn, PromptArea, StatusBar, UserTurn

HELP = """[b]commands[/b]
  [cyan]/new[/]              start a new conversation (also Ctrl+N)
  [cyan]/system <text>[/]    set or replace the system prompt
  [cyan]/help[/]             this list
  [cyan]/exit[/], [cyan]/quit[/]      leave"""

def _date_line() -> str:
    # Kept last in the system prompt: it's the only volatile part, so the stable instruction
    # prefix above it stays identical day to day and remains cacheable by llama-server.
    now = datetime.now().astimezone()
    return f"Current date: {now:%A, %Y-%m-%d}. Treat this as today when reasoning about time."


def resolve_whisper_dir(cwd_whisper: Path | None = None) -> Path:
    """Dev fallback: ./whisper if it holds the server binary, else the per-user data dir."""
    local = cwd_whisper if cwd_whisper is not None else Path("whisper")
    if (local / "whisper-server.exe").exists():
        return local
    return default_whisper_dir()


def resolve_workspace(
    conv_workspace: str | None,
    settings_default: str | None,
    config_workspace: str | None,
    cwd: str,
) -> str:
    """Pure precedence helper: conversation > settings default > config/CLI > cwd.

    Each source is treated as absent when falsy (None or empty string).
    Extracted so it can be unit-tested without constructing App.
    """
    return conv_workspace or settings_default or config_workspace or cwd


class Config:
    def __init__(
        self, url, model, system, db_path=None, web=True, memory=True,
        voice=True, whisper_bin=None, whisper_model=None, whisper_url=None,
        fs=True, workspace=None,
    ):
        self.url = url
        self.model = model
        self.system = system
        self.db_path = db_path
        self.web = web
        self.memory = memory
        self.voice = voice
        self.whisper_bin = whisper_bin
        self.whisper_model = whisper_model
        self.whisper_url = whisper_url
        self.fs = fs
        self.workspace = workspace


class LlamaTUI(App):
    CSS_PATH = "styles.tcss"
    TITLE = "llamatui"

    BINDINGS = [
        Binding("ctrl+n", "new_chat", "New"),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+comma", "open_settings", "Settings"),
        Binding("ctrl+d", "delete_chat", "Delete"),
        Binding("ctrl+r", "dictate", "Dictate"),
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, config: Config, cli_overrides: dict | None = None) -> None:
        super().__init__()
        self.config = config
        self._cli_overrides = cli_overrides or {}
        self.settings = DEFAULTS                 # replaced with the resolved value in on_mount
        self.model_label = humanize_model_name(config.model)
        self.context_window: int | None = None
        self.agent = None
        self._builder: AgentBuilder | None = None
        self._busy = False
        self.workspace = None
        self._approve_all = False
        self._pause_s = 0.0
        self.store: Store | None = None
        self.conversation: Conversation | None = None
        self.web_tool = None
        self.web_enabled = False
        self.memory: Memory | None = None
        self.whisper: WhisperServer | None = None
        self.dictation: Dictation | None = None
        self.voice: VoiceInput | None = None
        self.voice_enabled = False

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
        self.settings = load_settings(paths.settings_path(), self._cli_overrides)
        # One shared connection: Store owns conversations, KnowledgeGraph owns the graph.
        conn = connect(self.config.db_path)
        self.store = Store(conn)
        detected = detect_model_id(self.config.url)
        if detected:
            self.config.model = detected
            self.model_label = humanize_model_name(detected)
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

        if self.config.voice:
            self.whisper = WhisperServer(
                whisper_dir=resolve_whisper_dir(),
                bin_path=self.config.whisper_bin,
                model_path=self.config.whisper_model,
                url=self.config.whisper_url,
            )
            recorder = build_recorder()
            if recorder is not None and self.whisper.available():
                self.dictation = Dictation(
                    recorder=recorder,
                    transcriber=self.whisper,
                    run_bg=self._dictation_bg,
                    on_text=self._insert_transcript,
                    on_state=self._voice_state,
                    on_note=self._voice_note,
                )
                self.voice = VoiceInput(
                    self.dictation,
                    self._schedule_interval,
                    mode=self.settings.voice_mode,
                    d=keyboard_initial_delay_s(),
                    on_note=self._voice_note,
                )
                self.voice_enabled = True

        self._builder = AgentBuilder(
            self.config.url, self.config.model,
            web_tool=self.web_tool if self.web_enabled else None,
            memory=self.memory,
        )
        self._rebuild_agent()
        self._refresh_sidebar()
        self.query_one("#prompt", PromptArea).focus()

        web = (
            f"web search [b]on[/]{'' if exa_key_present() else ' (keyless)'}"
            if self.web_enabled
            else "web search [b]off[/]"
        )
        mem = f"memory [b]{'on' if self.memory is not None else 'off'}[/]"
        voice = f"voice [b]{'on' if self.voice_enabled else 'off'}[/]"
        self._write_system(
            f"Connected to [b]{self.config.url}[/]  ·  model [b]{self.model_label}[/]"
            + (f"  ·  ctx {self.context_window:,}" if self.context_window else "")
            + f"  ·  {web}  ·  {mem}  ·  {voice}"
            + "\nType a message, or [cyan]/help[/] for commands."
        )
        self._status("ready", connected=connected)

    async def on_unmount(self) -> None:
        if self.whisper is not None:
            try:
                self.whisper.close()
            except Exception:
                pass
        if self.web_tool is not None:
            try:
                await self.web_tool.close()
            except Exception:
                pass
        if self.store is not None:
            self.store.close()

    @work(thread=True, group="dictation")
    def _dictation_bg(self, task, done) -> None:
        result = task()
        self.call_from_thread(done, result)

    def _schedule_interval(self, interval: float, callback):
        """The interval seam VoiceInput schedules its cap/hold poll through (see voice.py).
        Returns the timer's zero-arg ``stop`` as the cancel handle."""
        return self.set_interval(interval, callback).stop

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

    def _resolve_workspace(self) -> str:
        """Precedence: conversation.workspace > settings.default_workspace > config.workspace > cwd."""
        conv = self.conversation.workspace if self.conversation else None
        return resolve_workspace(
            conv,
            self.settings.default_workspace,
            self.config.workspace,
            str(Path.cwd()),
        )

    def _rebuild_workspace(self) -> None:
        """Rebuild self.workspace from the resolved root, or set to None when fs is disabled."""
        if not self.config.fs:
            self.workspace = None
            return
        from .filesystem import Workspace
        self.workspace = Workspace(self._resolve_workspace())
        # PIN the resolved root onto the conversation the first time we know it.
        # Precedence (conv > settings > config > cwd) means once pinned the root is stable
        # regardless of later Settings.default_workspace changes.
        if self.conversation is not None and not self.conversation.workspace:
            self.conversation.workspace = str(self.workspace.root)
            if self.conversation.id is not None:
                self.store.set_workspace(self.conversation.id, self.conversation.workspace)

    def _rebuild_agent(self) -> None:
        """Conversation boundary: recompute the semi-volatile prompt + tools, rebuild the agent.
        Delegates the assembly + cache-prefix discipline to AgentBuilder."""
        self._rebuild_workspace()
        self.agent = self._builder.rebuild(
            persona=self.conversation.system_prompt, volatile=_date_line(),
            settings=self.settings, workspace=self.workspace,
        )

    def _apply_agent(self) -> None:
        """Mid-turn sampling change: rebuild the agent from the cached prompt (cache prefix intact).
        Safe mid-stream: the generate worker holds the old agent's iterator (see CONTEXT.md)."""
        self.agent = self._builder.apply_sampling(self.settings)

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
        elif cmd == "/system":
            self.conversation.system_prompt = rest or None
            self._rebuild_agent()
            self._write_system(f"[dim](system prompt {'updated' if rest else 'cleared'})[/]")
        else:
            self._write_system(f"[red]unknown command:[/] {cmd}  —  try [cyan]/help[/]")

    async def _send(self, text: str) -> None:
        await self.transcript.mount(UserTurn(text))
        self.conversation.append_user(text)
        turn = AssistantTurn(show_thinking=self.settings.show_thinking)
        await self.transcript.mount(turn)
        self.transcript.scroll_end(animate=False)
        self.generate(turn, text)

    # ---- streaming worker (adapter over TurnStream + TurnView) -----------
    def _on_turn_status(self, phase: str, rate: float) -> None:
        """TurnView fires this on each live render (throttled): refresh the status line and keep
        the transcript pinned to the bottom. The StatusBar and transcript are App-global."""
        was = getattr(self, "_running_command", False)
        self._running_command = phase == "running"
        # When a command finishes (was running, now not) clear the cancel event so a later
        # command in the same turn isn't pre-cancelled by a leftover set() from a prior one.
        if was and not self._running_command and self.workspace and self.workspace.cancel_event:
            self.workspace.cancel_event.clear()
        self._status(f"{phase}…", detail=f"~{rate:.0f} tok/s", connected=True)
        self.transcript.scroll_end(animate=False)

    @work(exclusive=True, group="gen")
    async def generate(self, turn: AssistantTurn, user_text: str) -> None:
        self._busy = True
        self._approve_all = False
        self._running_command = False
        self._pause_s = 0.0                       # cumulative human-approval time, excluded from elapsed
        stream = TurnStream()
        view = TurnView(turn, on_status=self._on_turn_status)
        if self.workspace is not None:
            self.workspace.on_output = turn.append_command_output   # sink → the in-flight chip
            self.workspace.cancel_event = asyncio.Event()
        # Per-turn resume carrier: the session holds the in-flight assistant message (including any
        # pending function_approval_request), so resuming on it preserves the run state. Verified:
        # there is no `get_new_thread()`; the framework's per-run carrier is `create_session()`
        # returning an AgentSession (agent_framework/_agents.py:411, _sessions.py:746).
        session = self.agent.create_session()
        pending = self.conversation.messages_for_agent()   # first segment: user + prior answers

        try:
            while True:
                stream_obj = self.agent.run(pending, session=session, stream=True)
                async for update in stream_obj:
                    stream.ingest(update)
                    view.reflect(stream.state)
                final = await stream_obj.get_final_response()
                requests = list(final.user_input_requests)
                if not requests:
                    break
                responses = await self._resolve_approvals(requests, view)
                # Resume on the SAME session (it holds the assistant function_call) + SAME agent
                # (KV prefix intact). Submit ONLY the approval responses.
                pending = [Message(role="user", contents=responses)]
        except Exception as exc:
            view.reflect(stream.state, force=True)
            view.error(exc)
            self._status("error", connected=False)
            self.conversation.undo_last_user()
            self._busy = False
            return

        view.reflect(stream.state, force=True)
        st = stream.state
        m = M.extract(
            st.usage_details, st.timings, ttft_s=st.ttft_s,
            elapsed_s=stream.elapsed() - self._pause_s,   # exclude human-approval time (Task 3.5)
            answer_chars=len(st.answer), context_window=self.context_window,
        )
        metrics_line = M.format_oneline(m)
        view.finalize(st, metrics_line)

        # persist the completed exchange (history + storage together)
        self.conversation.append_assistant(
            user_text=user_text,
            answer=strip_tool_noise(st.answer),
            reasoning=st.reasoning or None,
            metrics=metrics_blob(metrics_line),
        )
        self._refresh_sidebar()

        ctx = ""
        if m.context_frac is not None:
            ctx = f"ctx {m.context_used:,}/{m.context_window:,} ({m.context_frac*100:.0f}%)"
        self._status("ready", detail=ctx, connected=True)
        self._busy = False

    async def _resolve_approvals(self, requests: list, view) -> list:
        """Show the modal (unless already 'approve all'), return approval-response contents.
        Times the human pause so generate() can exclude it from elapsed (Task 3.5)."""
        import time
        run_cmd = [r for r in requests if getattr(r.function_call, "name", "") == "run_command"]
        typed = [r for r in requests if r not in run_cmd]
        decided: dict = {}
        to_prompt = list(run_cmd)               # run_command is NEVER blanket-approved
        if self._approve_all:
            decided.update({r.id: True for r in typed})
        else:
            to_prompt += typed
        if to_prompt:
            self._status("awaiting approval")
            t0 = time.monotonic()
            result = await self.push_screen_wait(
                ApprovalModal(to_prompt, workspace=self.workspace)
            )
            self._pause_s += time.monotonic() - t0
            result = result or {r.id: False for r in to_prompt}
            if result.pop("__all__", False):
                self._approve_all = True
            decided.update(result)
        return [r.to_function_approval_response(approved=bool(decided.get(r.id, False)))
                for r in requests]

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
                turn = AssistantTurn(show_thinking=self.settings.show_thinking)
                await self.transcript.mount(turn)
                TurnView(turn).load_saved(
                    answer=row["content"], reasoning=row["reasoning"], metrics=row["metrics"],
                )
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
        if not self._busy:
            return
        if getattr(self, "_running_command", False) and self.workspace and self.workspace.cancel_event:
            self.workspace.cancel_event.set()       # kill the command; the runner returns "cancelled",
            self._status("cancelling command…")     # the agentic loop continues — turn survives
            return
        self.workers.cancel_group(self, "gen")
        self._busy = False
        self._status("cancelled", connected=True)
        if self.conversation is not None:
            self.conversation.undo_last_user()

    def on_prompt_area_dictate(self, event: PromptArea.Dictate) -> None:
        self.action_dictate()

    def action_dictate(self) -> None:
        if self.voice is None:
            self._voice_note("voice off — run: llamatui --setup-voice")
            return
        self.voice.key()

    def _insert_transcript(self, text: str) -> None:
        prompt = self.query_one("#prompt", PromptArea)
        prompt.insert_transcript(text)
        prompt.focus()

    def _voice_state(self, state) -> None:
        labels = {
            State.IDLE: "",
            State.RECORDING: "🎙 recording",
            State.TRANSCRIBING: "transcribing…",
        }
        self.query_one("#status", StatusBar).show(voice=labels[state])

    def _voice_note(self, msg: str) -> None:
        self.query_one("#status", StatusBar).show(voice=msg)
        self.set_timer(3.0, lambda: self.query_one("#status", StatusBar).show(voice=""))

    def action_open_settings(self) -> None:
        self.push_screen(SettingsScreen(self.settings), self._on_settings_closed)

    def _on_settings_closed(self, result: "Settings | None") -> None:
        if result is None:
            return
        changed = changed_fields(self.settings, result)
        if not changed:
            return
        self.settings = result
        if changed.keys() & SAMPLING_FIELDS:
            self._apply_agent()                       # cached instructions → cache prefix intact
        if "show_thinking" in changed:
            for turn in self.query(AssistantTurn):
                turn.set_thinking_visible(result.show_thinking)
        if "voice_mode" in changed and self.voice is not None:
            self.voice.set_mode(result.voice_mode)    # discards any in-flight recording, re-arms
        if "default_workspace" in changed:
            self._rebuild_workspace()
        try:
            save_changes(paths.settings_path(), changed)
        except OSError as exc:
            self._voice_note(f"settings not saved: {exc}")

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display
