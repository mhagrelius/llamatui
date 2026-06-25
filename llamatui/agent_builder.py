"""AgentBuilder — assembles the ChatAgent from the enabled features, the persona, and sampling.

This is the composition root above the wire-level :func:`client.build_agent`: it gathers the
tools and their guidance from the enabled features (web, memory), splices the ambient memory
preamble, and composes the system prompt — then owns the **cache-prefix split** that keeps
llama-server's KV prefix alive:

- :meth:`rebuild` recomputes the semi-volatile prompt (persona + capabilities + ambient + the
  volatile date) and rebuilds the agent. Called only at conversation boundaries.
- :meth:`apply_sampling` rebuilds the agent from the *cached* prompt + new sampling, so a
  mid-turn sampling change never touches the prompt — the cached KV prefix survives.

The guidance→prompt step is isolated in :meth:`_capabilities`; each feature's when-to-use note
lives in the module that owns the tool (``tools.WEB_SEARCH_GUIDANCE``, ``memory.MEMORY_GUIDANCE``),
so adopting agent-framework skills later moves out through that one seam.
"""

from __future__ import annotations

from .client import build_agent
from .filesystem import FILESYSTEM_GUIDANCE
from .instructions import build_instructions
from .memory import MEMORY_GUIDANCE
from .tools import WEB_SEARCH_GUIDANCE

DEFAULT_SYSTEM = (
    "You are Tilde, a personal AI assistant who lives entirely on the user's own machine, running "
    "locally through llama.cpp. You look after one person, your principal, and you are here for "
    "whatever they need: answering questions, thinking through problems, writing and editing, "
    "coding, planning, or looking things up.\n\n"
    "Voice: warm, easygoing, and genuinely on their side. Talk with them like a sharp, friendly "
    "colleague, not a corporate help desk. A little humour is welcome. Warm does not mean soft: "
    "you have opinions and you use them. Write in plain, direct prose.\n\n"
    "How you work:\n"
    "- Lead with the answer or your recommendation. Context and caveats come after, not before.\n"
    "- Be concise by default and match their energy. A quick question gets a quick answer; a hard "
    "problem gets real depth. Use Markdown and code blocks to stay readable.\n"
    "- Give concrete, immediately usable deliverables rather than abstract advice.\n"
    "- Push back once. If a plan has a gap or an assumption looks wrong, say so plainly. Make your "
    "case once, then if they still want to proceed, drop it and execute. You are the advisor, not "
    "the gatekeeper.\n"
    "- Own what you take on. If something does not work, come back with what you tried and what to "
    "do next, not just 'I couldn't.'\n"
    "- Think ahead. When you finish, flag what they will likely need next.\n"
    "- When you are unsure, say so plainly. Never invent facts, quotes, citations, or URLs. A clear "
    "'I don't know' beats a confident guess.\n\n"
    "What you don't do:\n"
    "- No hollow openers like 'Great question' or 'Absolutely,' and no restating their question.\n"
    "- No filler apologies. Apologize only when you actually got something wrong.\n"
    "- No hedging everything to death. Take a position; correct course if you turn out wrong.\n"
    "- No asking permission for routine steps. Do the thing and say what you did.\n\n"
    "Conventions:\n"
    "- Use 'I' naturally. Do not announce your name unless they ask who you are.\n"
    "- Skip the greeting if they open with a question; just answer.\n"
    "- Ask a clarifying question only when the request is genuinely ambiguous. Otherwise make a "
    "sensible assumption, state it, and go.\n\n"
    "Everything here is private and stays on their machine, so be candid and respect their time."
)


class AgentBuilder:
    def __init__(self, base_url: str, model: str, *, web_tool=None, memory=None) -> None:
        self._base_url = base_url
        self._model = model
        self._web_tool = web_tool
        self._memory = memory
        self._workspace = None
        self._instructions: str = ""
        self._tools: list = []

    @property
    def instructions(self) -> str:
        return self._instructions

    @property
    def tools(self) -> list:
        return list(self._tools)

    # ---- boundary: recompute the semi-volatile prompt --------------------
    def rebuild(self, *, persona: str | None, volatile: str | None, settings, workspace=None):
        self._workspace = workspace
        tools, notes, ambient = self._capabilities()
        lead = []
        if self._workspace is not None:
            lead.append(self._workspace.workspace_line())
        capabilities = lead + (
            ["Your tools (use them deliberately):\n\n" + "\n\n".join(notes)] if notes else []
        )
        self._instructions = build_instructions(
            persona=persona or DEFAULT_SYSTEM,
            capabilities=capabilities,
            ambient=ambient,
            volatile=volatile,
        )
        self._tools = tools
        return self._build(settings)

    def _capabilities(self) -> tuple[list, list[str], str | None]:
        """Turn the enabled features into (tools, when-to-use notes, ambient block). The one seam
        that an agent-framework skills adoption will replace; everything else here is skill-agnostic.
        Each note is owned by the module that owns the tool."""
        tools: list = []
        notes: list[str] = []
        ambient: str | None = None
        if self._web_tool is not None:
            tools.append(self._web_tool)
            notes.append(WEB_SEARCH_GUIDANCE)
        if self._memory is not None:
            tools.extend(self._memory.build_tools())
            notes.append(MEMORY_GUIDANCE)
            ambient = self._memory.preamble()
        if self._workspace is not None:
            tools.extend(self._workspace.build_tools())
            notes.append(FILESYSTEM_GUIDANCE)
        return tools, notes, ambient

    # ---- mid-turn: reuse the cached prompt -------------------------------
    def apply_sampling(self, settings):
        return self._build(settings)

    def _build(self, settings):
        return build_agent(
            base_url=self._base_url,
            model=self._model,
            instructions=self._instructions or None,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            top_p=settings.top_p,
            thinking_budget=settings.thinking_budget,
            tools=self._tools or None,
        )
