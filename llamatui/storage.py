"""SQLite persistence for conversations and messages (elia-style).

A single file under the user data dir holds every conversation so they survive restarts.
Messages keep the answer, the thinking, and a small JSON metrics blob so a reloaded turn
looks like it did live.

The knowledge-graph half of the database lives in its own deep module
(:class:`~llamatui.graph.KnowledgeGraph`); both share one connection opened by :func:`connect`.
``Store`` owns only conversations and messages.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import platformdirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL DEFAULT 'New conversation',
    system_prompt TEXT,
    model         TEXT,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    reasoning       TEXT,
    metrics         TEXT,
    created_at      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> Path:
    d = Path(platformdirs.user_data_dir("llamatui", appauthor=False))
    d.mkdir(parents=True, exist_ok=True)
    return d / "conversations.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open the single shared connection used by both ``Store`` and ``KnowledgeGraph``.

    One connection, one file, accessed from the main thread — so the two modules can each own
    their own tables without a second connection or cross-thread locking.
    """
    conn = sqlite3.connect(Path(path) if path else default_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class Store:
    """Conversations and messages only. Takes the shared connection from :func:`connect`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.db = conn
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ---- conversations ---------------------------------------------------
    def create_conversation(self, title: str, system_prompt: str | None, model: str | None) -> int:
        ts = _now()
        cur = self.db.execute(
            "INSERT INTO conversations (title, system_prompt, model, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (title[:120], system_prompt, model, ts, ts),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def list_conversations(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT id, title, model, updated_at FROM conversations ORDER BY updated_at DESC"
        ).fetchall()

    def get_conversation(self, conv_id: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()

    def rename_conversation(self, conv_id: int, title: str) -> None:
        self.db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title[:120], conv_id))
        self.db.commit()

    def touch(self, conv_id: int) -> None:
        self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conv_id))
        self.db.commit()

    def delete_conversation(self, conv_id: int) -> None:
        self.db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self.db.commit()

    # ---- messages --------------------------------------------------------
    def add_message(
        self,
        conv_id: int,
        role: str,
        content: str,
        reasoning: str | None = None,
        metrics: dict | None = None,
    ) -> int:
        cur = self.db.execute(
            "INSERT INTO messages (conversation_id, role, content, reasoning, metrics, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (conv_id, role, content, reasoning, json.dumps(metrics) if metrics else None, _now()),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def get_messages(self, conv_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT role, content, reasoning, metrics FROM messages"
            " WHERE conversation_id = ? ORDER BY id",
            (conv_id,),
        ).fetchall()
