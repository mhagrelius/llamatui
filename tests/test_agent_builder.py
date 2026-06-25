"""AgentBuilder's interface is the test surface: it assembles the agent from the enabled features
(web, memory) plus the conversation persona and current sampling, and owns the cache-prefix split
— rebuild() at conversation boundaries (recomputes the semi-volatile prompt), apply_sampling()
mid-turn (reuses the cached prompt so the KV prefix survives). No Textual, no live server:
build_agent constructs a client object without connecting, and the features are fakes.
"""

from __future__ import annotations

from dataclasses import replace

from llamatui.agent_builder import AgentBuilder
from llamatui.settings import DEFAULTS


class FakeMemory:
    def __init__(self, tools=None, preamble="Background: the user likes tea."):
        self._tools = tools if tools is not None else [object()]
        self._preamble = preamble
    def build_tools(self):
        return list(self._tools)
    def preamble(self):
        return self._preamble


def _settings(**kw):
    return replace(DEFAULTS, **kw)


def test_rebuild_composes_persona_and_returns_agent():
    b = AgentBuilder("http://x", "m", web_tool=None, memory=None)
    agent = b.rebuild(persona="YOU ARE TILDE", volatile="Current date: today", settings=DEFAULTS)
    assert "YOU ARE TILDE" in b.instructions
    assert b.instructions.endswith("Current date: today")   # volatile slot lands last
    assert agent is not None


def test_persona_falls_back_to_default_system():
    b = AgentBuilder("http://x", "m")
    b.rebuild(persona=None, volatile="d", settings=DEFAULTS)
    assert "You are Tilde" in b.instructions                 # built-in fallback persona


def test_apply_sampling_preserves_prompt_rebuild_changes_it():
    b = AgentBuilder("http://x", "m")
    b.rebuild(persona="P", volatile="D1", settings=DEFAULTS)
    i1 = b.instructions
    b.apply_sampling(_settings(temperature=0.1))
    assert b.instructions == i1                              # sampling change → prompt untouched
    b.rebuild(persona="P", volatile="D2", settings=DEFAULTS)
    assert b.instructions != i1                              # boundary recompute → new prompt


# ---- capabilities (the isolated guidance seam) ---------------------------
def test_no_features_no_tools_heading_or_tools():
    b = AgentBuilder("http://x", "m")
    b.rebuild(persona="P", volatile="d", settings=DEFAULTS)
    assert "Your tools" not in b.instructions
    assert b.tools == []


def test_web_feature_adds_note_and_tool():
    from llamatui.tools import WEB_SEARCH_GUIDANCE
    web = object()
    b = AgentBuilder("http://x", "m", web_tool=web)
    b.rebuild(persona="P", volatile="d", settings=DEFAULTS)
    assert "Your tools" in b.instructions
    assert WEB_SEARCH_GUIDANCE in b.instructions
    assert web in b.tools


def test_memory_feature_adds_note_tools_and_ambient():
    from llamatui.memory import MEMORY_GUIDANCE
    t1, t2 = object(), object()
    mem = FakeMemory(tools=[t1, t2], preamble="Background: the user likes tea.")
    b = AgentBuilder("http://x", "m", memory=mem)
    b.rebuild(persona="P", volatile="d", settings=DEFAULTS)
    assert MEMORY_GUIDANCE in b.instructions
    assert "Background: the user likes tea." in b.instructions   # ambient spliced
    assert t1 in b.tools and t2 in b.tools


def test_ambient_recomputed_each_rebuild():
    mem = FakeMemory(preamble="first")
    b = AgentBuilder("http://x", "m", memory=mem)
    b.rebuild(persona="P", volatile="d", settings=DEFAULTS)
    assert "first" in b.instructions
    mem._preamble = "second"                                 # memory learned something new
    b.rebuild(persona="P", volatile="d", settings=DEFAULTS)
    assert "second" in b.instructions and "first" not in b.instructions


from llamatui.filesystem import Workspace


def test_workspace_line_is_in_instructions(tmp_path):
    b = AgentBuilder("http://x/v1", "m")
    b.rebuild(persona="P", volatile="D", settings=DEFAULTS, workspace=Workspace(tmp_path, shell="PowerShell"))
    assert str(tmp_path.resolve()) in b.instructions
