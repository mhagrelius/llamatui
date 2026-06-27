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

from . import paths
from .images import sha256_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL DEFAULT 'New conversation',
    system_prompt TEXT,
    model         TEXT,
    workspace     TEXT,
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
CREATE TABLE IF NOT EXISTS message_images (
    id         INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    ordinal    INTEGER NOT NULL,
    media_type TEXT    NOT NULL,
    sha256     TEXT    NOT NULL,
    source     TEXT,
    created_at TEXT    NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> Path:
    d = paths.user_data_dir()
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

    def __init__(self, conn, *, images_dir=None) -> None:
        self.db = conn
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(conversations)")}
        if "workspace" not in cols:
            self.db.execute("ALTER TABLE conversations ADD COLUMN workspace TEXT")
        self.db.commit()
        if images_dir is not None:
            self._images_dir = Path(images_dir)
        else:
            row = self.db.execute("PRAGMA database_list").fetchone()
            db_file = row[2] if row else ""
            if db_file:
                self._images_dir = Path(db_file).parent / "images"
            else:
                self._images_dir = default_db_path().parent / "images"

    def close(self) -> None:
        self.db.close()

    # ---- conversations ---------------------------------------------------
    def create_conversation(self, title: str, system_prompt: str | None, model: str | None, workspace: str | None = None) -> int:
        ts = _now()
        cur = self.db.execute(
            "INSERT INTO conversations (title, system_prompt, model, workspace, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (title[:120], system_prompt, model, workspace, ts, ts),
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

    def set_workspace(self, conv_id: int, path: str | None) -> None:
        self.db.execute("UPDATE conversations SET workspace = ? WHERE id = ?", (path, conv_id))
        self.db.commit()

    def touch(self, conv_id: int) -> None:
        self.db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conv_id))
        self.db.commit()

    def delete_conversation(self, conv_id: int) -> None:
        shas = {
            r["sha256"]
            for r in self.db.execute(
                "SELECT DISTINCT mi.sha256 FROM message_images mi"
                " JOIN messages m ON m.id = mi.message_id WHERE m.conversation_id = ?",
                (conv_id,),
            ).fetchall()
        }
        self.db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        self.db.commit()
        self._sweep_orphan_images(shas)

    def add_image(self, message_id: int, ordinal: int, media_type: str, data: bytes, source: str) -> None:
        sha = sha256_id(data)
        self._images_dir.mkdir(parents=True, exist_ok=True)
        path = self._images_dir / f"{sha}.png"
        if not path.exists():
            path.write_bytes(data)
        self.db.execute(
            "INSERT INTO message_images (message_id, ordinal, media_type, sha256, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, ordinal, media_type, sha, source, _now()),
        )
        self.db.commit()

    def get_images(self, message_id: int) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT ordinal, media_type, sha256, source FROM message_images"
            " WHERE message_id = ? ORDER BY ordinal",
            (message_id,),
        ).fetchall()

    def image_bytes(self, sha: str) -> bytes:
        return (self._images_dir / f"{sha}.png").read_bytes()

    def _sweep_orphan_images(self, shas: set[str]) -> None:
        for sha in shas:
            still = self.db.execute(
                "SELECT 1 FROM message_images WHERE sha256 = ? LIMIT 1", (sha,)
            ).fetchone()
            if not still:
                (self._images_dir / f"{sha}.png").unlink(missing_ok=True)

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
            "SELECT id, role, content, reasoning, metrics FROM messages"
            " WHERE conversation_id = ? ORDER BY id",
            (conv_id,),
        ).fetchall()
