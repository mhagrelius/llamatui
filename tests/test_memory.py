"""Memory is the thin surface over the graph: tool wording, the preamble, and tool wiring.

The graph mechanics (storage, hybrid retrieval, scoring) are covered in test_graph.py; here we
build a real KnowledgeGraph and assert what the *model* sees.
"""

import llamatui.memory as memory
from llamatui.graph import KnowledgeGraph
from llamatui.memory import Memory
from llamatui.storage import connect

from test_graph import FakeEmbedder


def _memory(tmp_path):
    return Memory(KnowledgeGraph(connect(tmp_path / "m.db")))


# ---- tool wording -----------------------------------------------------------------------
def test_remember_wording(tmp_path):
    m = _memory(tmp_path)
    assert "Noted" in m.remember("prefers concise answers")
    assert "Already knew" in m.remember("prefers concise answers", subject="USER")
    note = m.remember("written in Python", subject="llamatui", related_to="user", relation="created")
    assert "llamatui created user" in note


def test_recall_and_forget_wording(tmp_path):
    m = _memory(tmp_path)
    m.remember("written in Python", subject="llamatui", subject_type="project")
    rendered = m.recall("python")
    assert "llamatui (project)" in rendered and "written in Python" in rendered
    assert "No memories" in m.recall("nonexistent topic")
    assert "Forgot everything about llamatui" in m.forget("llamatui")
    assert "Nothing to forget" in m.forget("llamatui")


def test_recall_renders_relations(tmp_path):
    m = _memory(tmp_path)
    m.remember("x", subject="llamatui", related_to="user", relation="created")
    assert "created → user" in m.recall("llamatui")


def test_attach_embedder_enables_semantic_recall(tmp_path):
    m = _memory(tmp_path)
    m.remember("adores spicy cuisine", subject="bob")
    assert "No memories" in m.recall("likes hot food")   # keyword-only miss
    m.attach_embedder(FakeEmbedder())                    # public seam → graph backfills
    assert "spicy" in m.recall("likes hot food")


# ---- ambient preamble -------------------------------------------------------------------
def test_preamble_none_when_empty(tmp_path):
    assert _memory(tmp_path).preamble() is None


def test_preamble_pins_user_first(tmp_path):
    m = _memory(tmp_path)
    for i in range(4):
        m.remember(f"alpha fact {i}", subject="alpha")  # more salient than user
    m.remember("prefers concise answers", subject="user")
    pre = m.preamble()
    assert pre.index("- user") < pre.index("- alpha")


def test_preamble_splits_background_and_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "_BG_ENTITIES", 1)  # force only the top entity into Background
    m = _memory(tmp_path)
    m.remember("fact one", subject="user")
    m.remember("fact two", subject="user")
    m.remember("brand new thing", subject="project-x")  # newest, low salience
    bg, recent = m.preamble().split("Recently learned:")
    assert "Background:" in bg and "user" in bg
    assert "brand new thing" in recent     # overflowed entity surfaces as Recent
    assert "fact one" not in recent        # Background facts aren't echoed


# ---- tool wiring ------------------------------------------------------------------------
def test_build_tools_shapes(tmp_path):
    tools = _memory(tmp_path).build_tools()
    assert [t.name for t in tools] == ["remember", "recall", "forget"]
    remember = tools[0]
    schema = remember.parameters() if callable(remember.parameters) else remember.parameters
    props = schema["properties"]
    assert "content" in schema["required"]
    assert props["subject"]["default"] == "user"
    assert "related_to" in props and "relation" in props
