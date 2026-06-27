"""The conversation as a single source of truth.

A running conversation has two faces that must stay coherent: the in-memory list of
Agent Framework ``Message``s the model actually sees, and the SQLite rows that let it
survive a restart. Previously the App kept those in sync by hand across five methods, with
the "history never carries reasoning" invariant enforced somewhere else again. This module
owns both faces behind one narrow interface, so the coherence rules live in one place and
are testable against a temp database with no Textual and no server.

Persistence timing mirrors the old behaviour: the user message joins the in-memory history
*before* the turn streams (so the model sees it), but nothing is written to the store until
the assistant's answer lands — :meth:`append_assistant` persists the whole exchange at once
and lazily creates the conversation row on the first successful turn.
"""

from __future__ import annotations

from .client import make_message
from .storage import Store


def _title_from(user_text: str) -> str:
    first = user_text.strip().splitlines()[0] if user_text.strip() else ""
    return first[:60] if first else "New conversation"


class Conversation:
    """Owns the agent-facing message list and its persistence together."""

    def __init__(self, store: Store, *, model: str | None = None) -> None:
        self._store = store
        self.model = model
        self.id: int | None = None
        self.title: str | None = None
        self.system_prompt: str | None = None
        self.workspace: str | None = None
        self._messages: list = []  # user + assistant *answer* only — never reasoning

    # ---- lifecycle -------------------------------------------------------
    def new(self, system_prompt: str | None) -> None:
        """Start a fresh, unsaved conversation carrying ``system_prompt`` forward."""
        self.id = None
        self.title = None
        self.system_prompt = system_prompt
        self.workspace = None
        self._messages = []

    def load(self, conv_id: int) -> list | None:
        """Load a saved conversation; return its stored rows for the view to render.

        Sets :attr:`system_prompt`/:attr:`title` and rebuilds the agent-facing history.
        Returns ``None`` (leaving state untouched) if the id is unknown.
        """
        conv = self._store.get_conversation(conv_id)
        if conv is None:
            return None
        self.id = conv_id
        self.title = conv["title"]
        self.system_prompt = conv["system_prompt"]
        self.workspace = conv["workspace"]
        rows = self._store.get_messages(conv_id)
        self._messages = [make_message(r["role"], r["content"]) for r in rows]
        return rows

    # ---- mutation --------------------------------------------------------
    def append_user(self, text: str, attachments: list | None = None) -> None:
        """Add the user's message (optionally with images) to in-memory history."""
        self._messages.append(make_message("user", text, attachments))

    def undo_last_user(self) -> None:
        """Drop a trailing un-answered user message (turn cancelled or errored)."""
        if self._messages and self._messages[-1].role == "user":
            self._messages.pop()

    def append_assistant(
        self,
        *,
        user_text: str,
        answer: str,
        reasoning: str | None,
        metrics: dict | None,
    ) -> None:
        """Record a completed exchange: append the answer and persist both rows.

        Creates the conversation row on first use (titled from ``user_text``).
        """
        self._messages.append(make_message("assistant", answer))
        if self.id is None:
            self.title = _title_from(user_text)
            self.id = self._store.create_conversation(
                self.title, self.system_prompt, self.model, workspace=self.workspace
            )
        self._store.add_message(self.id, "user", user_text)
        self._store.add_message(self.id, "assistant", answer, reasoning or None, metrics)
        self._store.touch(self.id)

    # ---- reads -----------------------------------------------------------
    def messages_for_agent(self) -> list:
        """The message list to hand to ``agent.run``."""
        return self._messages

    @property
    def is_saved(self) -> bool:
        return self.id is not None
