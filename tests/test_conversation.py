"""Conversation owns history + persistence; its interface is the test surface.

Everything here runs against a throwaway SQLite file — no Textual, no server.
"""

import io

from PIL import Image

from llamatui.client import make_message
from llamatui.conversation import Conversation
from llamatui.images import prepare_image
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


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def test_make_message_text_only_single_part():
    msg = make_message("user", "hi")
    assert len(msg.contents) == 1


def test_make_message_with_attachment_has_image_part():
    att = prepare_image(_png())
    msg = make_message("user", "look", [att])
    # text + framing + image
    assert len(msg.contents) == 3


def test_user_images_persist_and_rehydrate(tmp_path):
    from llamatui.storage import Store, connect
    from llamatui.conversation import Conversation
    from llamatui.images import prepare_image

    store = Store(connect(tmp_path / "c.db"))
    conv = Conversation(store, model="m")
    att = prepare_image(_png())
    conv.append_user("look", [att])
    conv.append_assistant(
        user_text="look", answer="ok", reasoning=None,
        metrics=None, user_attachments=[att]
    )

    reopened = Conversation(store)
    reopened.load(conv.id)
    user_msg = reopened.messages_for_agent()[0]
    assert len(user_msg.contents) == 3   # text + framing + image


from llamatui.compaction import CompactionConfig


async def test_compact_if_needed_below_threshold_noop(tmp_path):
    conv = Conversation(_store(tmp_path), model="m")
    conv.append_user("hi")
    res = await conv.compact_if_needed(0.10, CompactionConfig())
    assert res is None
    assert len(conv.messages_for_agent()) == 1


async def test_compact_if_needed_disabled_noop(tmp_path):
    conv = Conversation(_store(tmp_path), model="m")
    for i in range(20):
        conv._messages.append(make_message("user", f"q{i}"))
        conv._messages.append(make_message("assistant", f"a{i}"))
    res = await conv.compact_if_needed(0.95, CompactionConfig(enabled=False))
    assert res is None


async def test_compact_now_summarizes_regardless_of_toggle(tmp_path):
    conv = Conversation(_store(tmp_path), model="m")
    conv._messages.append(make_message("user", "FIRST"))
    for i in range(8):
        conv._messages.append(make_message("user", f"q{i}"))
        conv._messages.append(make_message("assistant", f"a{i}"))
    res = await conv.compact_now(CompactionConfig(keep_recent_turns=2, use_llm_summary=False, enabled=False))
    assert res.changed()
    assert len(conv.messages_for_agent()) < 17


async def test_compact_for_overflow_reaches_floor(tmp_path):
    conv = Conversation(_store(tmp_path), model="m")
    conv._messages.append(make_message("user", "FIRST"))
    for i in range(10):
        conv._messages.append(make_message("user", f"q{i}"))
        conv._messages.append(make_message("assistant", f"a{i}"))
    conv._messages.append(make_message("user", "current"))
    res = await conv.compact_for_overflow(CompactionConfig(keep_recent_turns=2, use_llm_summary=False))
    msgs = conv.messages_for_agent()
    from llamatui.compaction import _extract_text
    assert _extract_text(msgs[0]) == "FIRST"
    assert _extract_text(msgs[-1]) == "current"
    assert res.changed()
