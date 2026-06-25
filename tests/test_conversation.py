"""Conversation owns history + persistence; its interface is the test surface.

Everything here runs against a throwaway SQLite file — no Textual, no server.
"""

from llamatui.conversation import Conversation
from llamatui.storage import Store, connect


def _store(tmp_path):
    return Store(connect(tmp_path / "conversations.db"))


def test_exchange_round_trips_through_storage(tmp_path):
    store = _store(tmp_path)
    conv = Conversation(store, model="local")
    conv.system_prompt = "be terse"

    conv.append_user("hello")
    assert len(conv.messages_for_agent()) == 1  # in memory, not yet saved
    assert not conv.is_saved

    conv.append_assistant(
        user_text="hello", answer="hi", reasoning="(thinking)", metrics={"line": "x"}
    )
    assert conv.is_saved
    cid = conv.id

    msgs = conv.messages_for_agent()
    assert [m.role for m in msgs] == ["user", "assistant"]

    # Reload into a fresh Conversation and confirm coherence.
    reloaded = Conversation(store)
    rows = reloaded.load(cid)
    assert rows is not None
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert reloaded.system_prompt == "be terse"
    assert reloaded.title == "hello"


def test_reasoning_is_persisted_but_never_replayed_to_the_agent(tmp_path):
    store = _store(tmp_path)
    conv = Conversation(store)
    conv.append_user("q")
    conv.append_assistant(user_text="q", answer="the answer", reasoning="secret thoughts", metrics=None)

    reloaded = Conversation(store)
    rows = reloaded.load(conv.id)

    # The stored row keeps the reasoning...
    assistant_row = rows[1]
    assert assistant_row["reasoning"] == "secret thoughts"
    # ...but the agent-facing message carries only the answer text.
    assistant_msg = reloaded.messages_for_agent()[1]
    assert assistant_msg.contents[0].text == "the answer"


def test_undo_last_user_drops_unanswered_turn(tmp_path):
    store = _store(tmp_path)
    conv = Conversation(store)
    conv.append_user("oops")
    conv.undo_last_user()
    assert conv.messages_for_agent() == []
    conv.undo_last_user()  # no-op when there's nothing to undo
    assert conv.messages_for_agent() == []


def test_new_resets_but_carries_system_prompt_forward(tmp_path):
    store = _store(tmp_path)
    conv = Conversation(store)
    conv.new("persona")
    conv.append_user("hi")
    conv.append_assistant(user_text="hi", answer="yo", reasoning=None, metrics=None)
    assert conv.is_saved

    conv.new(conv.system_prompt)
    assert conv.id is None
    assert conv.title is None
    assert conv.system_prompt == "persona"
    assert conv.messages_for_agent() == []


def test_load_unknown_id_returns_none(tmp_path):
    store = _store(tmp_path)
    conv = Conversation(store)
    assert conv.load(999) is None
    assert conv.id is None


def test_workspace_column_roundtrips(tmp_path):
    s = Store(connect(tmp_path / "c.db"))
    cid = s.create_conversation("t", None, "m", workspace=str(tmp_path))
    assert s.get_conversation(cid)["workspace"] == str(tmp_path)
    s.set_workspace(cid, str(tmp_path / "sub"))
    assert s.get_conversation(cid)["workspace"] == str(tmp_path / "sub")


def test_workspace_persists_through_reload_despite_changed_settings(tmp_path):
    """A conversation persisted with workspace='A' must reload with workspace='A'
    even if the caller would now supply a different settings default 'B'.

    This proves the per-conversation workspace pin is stable end-to-end.
    """
    store = _store(tmp_path)
    conv = Conversation(store, model="local")
    workspace_a = str(tmp_path / "project-a")

    # First turn: save the conversation with workspace pinned to A.
    conv.append_user("hello")
    conv.workspace = workspace_a
    conv.append_assistant(user_text="hello", answer="hi", reasoning=None, metrics=None)
    assert conv.is_saved
    cid = conv.id

    # Persist the pinned workspace via set_workspace (mirrors what _rebuild_workspace does).
    store.set_workspace(cid, workspace_a)

    # Reload: the workspace must survive even though the "current settings default" would be B.
    reloaded = Conversation(store)
    reloaded.load(cid)
    assert reloaded.workspace == workspace_a

    # The resolve_workspace helper must confirm that the pinned conversation root beats 'B'.
    from llamatui.app import resolve_workspace
    workspace_b = str(tmp_path / "settings-b")
    resolved = resolve_workspace(reloaded.workspace, workspace_b, None, str(tmp_path / "cwd"))
    assert resolved == workspace_a, (
        f"Expected pinned workspace {workspace_a!r} to beat settings default {workspace_b!r}; "
        f"got {resolved!r}"
    )
