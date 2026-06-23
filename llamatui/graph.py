"""The knowledge graph — one deep module owning everything about *facts the assistant knows*.

Entities, observations (timestamped facts), and typed relations live here, together with the
FTS5 keyword index, the embedding vector cache, salience scoring, hydration, and **hybrid
retrieval** (BM25 keyword + semantic, fused with Reciprocal Rank Fusion). Callers talk to it
by *intent* — :meth:`observe`, :meth:`search`, :meth:`salient`, :meth:`recent`,
:meth:`forget`, :meth:`attach_embedder` — never in SQL. That narrow interface is the test
surface (`tests/test_graph.py`); the surrounding :class:`~llamatui.memory.Memory` module is a
thin wrapper that turns these into model-facing tools and an ambient preamble.

The embedder is an injectable seam. :func:`build_embedder` feature-detects the optional
``fastembed`` package and returns ``None`` when it's absent, in which case :meth:`search` is
keyword-only. Vectors are computed lazily and cached on the observation row, and the *entire*
embedding lifecycle (compute-on-write, backfill, cosine) is owned here — nothing reaches in.
"""

from __future__ import annotations

import math
import re
import sqlite3
from array import array
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,          -- display name as first seen; matched case-insensitively
    type       TEXT,                      -- free-text hint: person/project/preference/concept/...
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_name ON entities(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS observations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id  INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    content    TEXT    NOT NULL,
    embedding  BLOB,                       -- nullable; little-endian float32 vector cache
    pinned     INTEGER NOT NULL DEFAULT 0, -- 1 = always surface in the ambient preamble
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_entity ON observations(entity_id, id);
CREATE TABLE IF NOT EXISTS relations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id    INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    to_id      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    type       TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_relations_from ON relations(from_id);
CREATE INDEX IF NOT EXISTS idx_relations_to ON relations(to_id);
-- Keyword index over observations; rowid == observations.id. Plain (not contentless) so a
-- single-row DELETE works for forget/cascade without replaying old column values.
CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
    content, entity_name, tokenize='porter unicode61'
);
"""

# Retrieval tuning. Drop semantic matches below the floor so an off-topic query returns nothing
# rather than the nearest-but-irrelevant memory; BM25 hits are always a real lexical match.
_SEM_FLOOR = 0.35
_RRF_K = 60
# Recall caps: a heavily-loaded entity (e.g. the user, with dozens of facts) must not dump its
# whole profile on every query. A hit carries only the observations that matched, plus a bounded
# slice of its relations for context.
_HIT_OBS = 6
_HIT_RELS = 8

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fts_query(text: str) -> str:
    """Turn arbitrary user text into a safe FTS5 query: quoted terms joined by OR."""
    terms = _WORD_RE.findall(text or "")
    return " OR ".join(f'"{t}"' for t in terms)


# ---- value objects (the graph's vocabulary) ----------------------------------------------
@dataclass
class Entity:
    name: str
    type: str | None
    observations: list[str] = field(default_factory=list)
    relations: list[tuple[str, str, str]] = field(default_factory=list)  # (dir 'out'/'in', type, other)


@dataclass
class Recent:
    entity: str
    content: str
    type: str | None = None


@dataclass
class Observed:
    added: bool          # False when the fact was already known (deduped)
    related: bool        # whether a relation edge was created


@dataclass
class Forgotten:
    entity: str | None   # set when a whole entity was dropped
    facts: int           # number of individual observations removed


# ---- embedding seam -----------------------------------------------------------------------
@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text."""
        ...


class FastEmbedEmbedder:
    """Default embedder: a small local ONNX model via ``fastembed`` (CPU, no server)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding  # imported lazily; optional dependency

        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.embed(texts)]


def build_embedder() -> Embedder | None:
    """The default embedder, or ``None`` if ``fastembed`` isn't installed (keyword-only)."""
    try:
        return FastEmbedEmbedder()
    except Exception:
        return None


def _pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


def _unpack(blob: bytes) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return a.tolist()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _rrf(rankings: list[list[int]], k: int = _RRF_K) -> list[int]:
    """Reciprocal Rank Fusion: merge ranked id lists into one. Scale-free, no tuning."""
    score: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            score[item] = score.get(item, 0.0) + 1.0 / (k + rank + 1)
    return sorted(score, key=lambda i: score[i], reverse=True)


# ---- the module --------------------------------------------------------------------------
class KnowledgeGraph:
    """A persistent knowledge graph over a shared SQLite connection.

    Shares its connection with :class:`~llamatui.storage.Store` (one file, one connection,
    single-threaded access); it creates its own tables and never touches the conversation ones.
    """

    def __init__(self, conn: sqlite3.Connection, embedder: Embedder | None = None) -> None:
        self.db = conn
        self._embedder = embedder
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        # Add columns introduced after a DB was first created (CREATE IF NOT EXISTS won't).
        cols = [r["name"] for r in self.db.execute("PRAGMA table_info(observations)").fetchall()]
        if "pinned" not in cols:
            self.db.execute("ALTER TABLE observations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")

    # ---- writing ---------------------------------------------------------
    def observe(
        self,
        subject: str,
        content: str,
        *,
        subject_type: str | None = None,
        related_to: str | None = None,
        relation: str | None = None,
        pin: bool = False,
    ) -> Observed:
        """Record a fact about ``subject`` (auto-creating it), optionally linking it to another
        entity. Dedupes identical facts; caches an embedding when an embedder is attached.

        ``pin=True`` marks the fact *core*, so it always surfaces in the ambient preamble — even
        for a fact already stored (it just flips the flag)."""
        eid = self._upsert_entity(subject, subject_type)
        oid, created = self._add_observation(eid, content)
        if oid is not None and pin:
            self.db.execute("UPDATE observations SET pinned = 1 WHERE id = ?", (oid,))
            self.db.commit()
        if created and self._embedder is not None:
            try:
                self._set_embedding(oid, _pack(self._embedder.embed([content])[0]))
            except Exception:
                pass  # the vector is a cache; never let it break a write
        related = False
        if related_to:
            tid = self._upsert_entity(related_to)
            self._add_relation(eid, tid, (relation or "related-to").strip())
            related = True
        return Observed(added=created, related=related)

    def pin(self, entity_name: str, contains: str | None = None) -> int:
        """Mark existing facts core. All of an entity's observations, or only those whose content
        matches ``contains``. Returns how many were pinned."""
        row = self.db.execute(
            "SELECT id FROM entities WHERE name = ? COLLATE NOCASE", (entity_name.strip(),)
        ).fetchone()
        if row is None:
            return 0
        if contains:
            cur = self.db.execute(
                "UPDATE observations SET pinned = 1 WHERE entity_id = ? AND content LIKE ?",
                (row["id"], f"%{contains}%"),
            )
        else:
            cur = self.db.execute(
                "UPDATE observations SET pinned = 1 WHERE entity_id = ?", (row["id"],)
            )
        self.db.commit()
        return cur.rowcount

    def forget(self, query: str) -> Forgotten:
        """Drop a whole entity when ``query`` is its exact name, else remove matching facts."""
        row = self.db.execute(
            "SELECT id, name FROM entities WHERE name = ? COLLATE NOCASE", (query.strip(),)
        ).fetchone()
        if row is not None:
            self._delete_entity(row["id"])
            return Forgotten(entity=row["name"], facts=0)
        ids = self._keyword_search(query, limit=20)
        for oid in ids:
            self._delete_observation(oid)
        return Forgotten(entity=None, facts=len(ids))

    def attach_embedder(self, embedder: Embedder) -> None:
        """Attach an embedder and vectorize any facts stored before it was available.

        Public on purpose: this is the whole embedding lifecycle in one call, so the App's
        adapter never reaches into graph internals. Run on the main thread (it writes SQLite)."""
        self._embedder = embedder
        missing = self.db.execute(
            "SELECT id, content FROM observations WHERE embedding IS NULL"
        ).fetchall()
        if not missing:
            return
        try:
            vectors = embedder.embed([r["content"] for r in missing])
        except Exception:
            return
        for row, vec in zip(missing, vectors):
            self._set_embedding(int(row["id"]), _pack(vec))

    @property
    def has_embedder(self) -> bool:
        return self._embedder is not None

    # ---- reading ---------------------------------------------------------
    def search(self, query: str, limit: int = 5) -> list[Entity]:
        """Top entities for a query via keyword(BM25) + semantic, fused with RRF.

        Each returned :class:`Entity` carries only the observations that *matched* (capped),
        not its full profile — so recalling against the user doesn't dump 30 facts every time.
        """
        wide = limit * 4
        keyword = self._keyword_search(query, limit=wide)
        semantic = self._semantic_search(query, limit=wide)
        fused = _rrf([keyword, semantic]) if semantic else keyword
        return self._hits(fused, limit)

    def salient(self, limit: int = 8) -> list[Entity]:
        """Entities ranked by salience = observation_count + relation_degree, then recency."""
        rows = self.db.execute(
            """
            SELECT e.id,
                   (SELECT COUNT(*) FROM observations o WHERE o.entity_id = e.id)
                 + (SELECT COUNT(*) FROM relations r WHERE r.from_id = e.id OR r.to_id = e.id)
                   AS salience
            FROM entities e
            ORDER BY salience DESC, e.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [self._entity(r["id"]) for r in rows]

    def recent(self, limit: int = 10) -> list[Recent]:
        """Newest observations across all entities."""
        rows = self.db.execute(
            "SELECT o.content, e.name AS entity, e.type FROM observations o"
            " JOIN entities e ON e.id = o.entity_id ORDER BY o.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Recent(entity=r["entity"], content=r["content"], type=r["type"]) for r in rows]

    def get(self, name: str) -> Entity | None:
        row = self.db.execute(
            "SELECT id FROM entities WHERE name = ? COLLATE NOCASE", (name.strip(),)
        ).fetchone()
        return self._entity(row["id"]) if row else None

    # ---- private SQL (the depth this module hides) -----------------------
    def _upsert_entity(self, name: str, type: str | None = None) -> int:
        name = name.strip()
        ts = _now()
        row = self.db.execute(
            "SELECT id FROM entities WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row is not None:
            if type:
                self.db.execute(
                    "UPDATE entities SET type = ?, updated_at = ? WHERE id = ?", (type, ts, row["id"])
                )
            else:
                self.db.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (ts, row["id"]))
            self.db.commit()
            return int(row["id"])
        cur = self.db.execute(
            "INSERT INTO entities (name, type, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, type or None, ts, ts),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def _add_observation(self, entity_id: int, content: str) -> tuple[int | None, bool]:
        """Insert a fact (or find the existing dup). Returns (observation_id, created)."""
        content = content.strip()
        if not content:
            return (None, False)
        existing = self.db.execute(
            "SELECT id FROM observations WHERE entity_id = ? AND content = ?", (entity_id, content)
        ).fetchone()
        if existing is not None:
            return (int(existing["id"]), False)
        name = self.db.execute(
            "SELECT name FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()["name"]
        cur = self.db.execute(
            "INSERT INTO observations (entity_id, content, created_at) VALUES (?, ?, ?)",
            (entity_id, content, _now()),
        )
        oid = int(cur.lastrowid)
        self.db.execute(
            "INSERT INTO observations_fts (rowid, content, entity_name) VALUES (?, ?, ?)",
            (oid, content, name),
        )
        self.db.commit()
        return (oid, True)

    def pinned(self, limit: int = 12) -> list[Recent]:
        """Core (pinned) facts, most salient entity first — the always-on ambient context."""
        rows = self.db.execute(
            """
            SELECT e.name AS entity, o.content,
                   (SELECT COUNT(*) FROM observations o2 WHERE o2.entity_id = e.id)
                 + (SELECT COUNT(*) FROM relations r WHERE r.from_id = e.id OR r.to_id = e.id)
                   AS salience
            FROM observations o JOIN entities e ON e.id = o.entity_id
            WHERE o.pinned = 1
            ORDER BY salience DESC, o.id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [Recent(entity=r["entity"], content=r["content"]) for r in rows]

    def _add_relation(self, from_id: int, to_id: int, type: str) -> None:
        if from_id == to_id or not type:
            return
        if self.db.execute(
            "SELECT 1 FROM relations WHERE from_id = ? AND to_id = ? AND type = ? COLLATE NOCASE",
            (from_id, to_id, type),
        ).fetchone() is not None:
            return
        self.db.execute(
            "INSERT INTO relations (from_id, to_id, type, created_at) VALUES (?, ?, ?, ?)",
            (from_id, to_id, type, _now()),
        )
        self.db.commit()

    def _set_embedding(self, observation_id: int, blob: bytes) -> None:
        self.db.execute("UPDATE observations SET embedding = ? WHERE id = ?", (blob, observation_id))
        self.db.commit()

    def _delete_entity(self, entity_id: int) -> None:
        obs_ids = [r["id"] for r in self.db.execute(
            "SELECT id FROM observations WHERE entity_id = ?", (entity_id,)
        ).fetchall()]
        self.db.executemany("DELETE FROM observations_fts WHERE rowid = ?", [(i,) for i in obs_ids])
        self.db.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        self.db.commit()

    def _delete_observation(self, observation_id: int) -> None:
        self.db.execute("DELETE FROM observations WHERE id = ?", (observation_id,))
        self.db.execute("DELETE FROM observations_fts WHERE rowid = ?", (observation_id,))
        self.db.commit()

    def _keyword_search(self, query: str, limit: int) -> list[int]:
        match = _fts_query(query)
        if not match:
            return []
        rows = self.db.execute(
            "SELECT rowid FROM observations_fts WHERE observations_fts MATCH ?"
            " ORDER BY bm25(observations_fts) LIMIT ?",
            (match, limit),
        ).fetchall()
        return [int(r["rowid"]) for r in rows]

    def _semantic_search(self, query: str, limit: int) -> list[int]:
        if self._embedder is None:
            return []
        rows = self.db.execute(
            "SELECT id, embedding FROM observations WHERE embedding IS NOT NULL"
        ).fetchall()
        if not rows:
            return []
        try:
            qv = self._embedder.embed([query])[0]
        except Exception:
            return []
        scored = [(int(r["id"]), _cosine(qv, _unpack(bytes(r["embedding"])))) for r in rows]
        scored = [t for t in scored if t[1] >= _SEM_FLOOR]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [oid for oid, _ in scored[:limit]]

    def _hits(self, obs_ids: list[int], limit: int) -> list[Entity]:
        """Group ranked matched observations by entity (rank-preserving), and return each entity
        carrying only those matched observations (capped) plus a bounded slice of its relations."""
        matched: dict[int, list[str]] = {}
        order: list[int] = []
        for oid in obs_ids:
            row = self.db.execute(
                "SELECT entity_id, content FROM observations WHERE id = ?", (oid,)
            ).fetchone()
            if row is None:
                continue
            eid = row["entity_id"]
            if eid not in matched:
                matched[eid] = []
                order.append(eid)
            if row["content"] not in matched[eid]:
                matched[eid].append(row["content"])
        hits: list[Entity] = []
        for eid in order[:limit]:
            e = self._entity(eid)
            e.observations = matched[eid][:_HIT_OBS]   # only the facts that matched the query
            e.relations = e.relations[:_HIT_RELS]
            hits.append(e)
        return hits

    def _entity(self, entity_id: int) -> Entity:
        erow = self.db.execute(
            "SELECT name, type FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        obs = [r["content"] for r in self.db.execute(
            "SELECT content FROM observations WHERE entity_id = ? ORDER BY id", (entity_id,)
        ).fetchall()]
        rels = self.db.execute(
            "SELECT 'out' AS dir, r.type, e.name AS other FROM relations r"
            " JOIN entities e ON e.id = r.to_id WHERE r.from_id = ?"
            " UNION ALL "
            "SELECT 'in' AS dir, r.type, e.name AS other FROM relations r"
            " JOIN entities e ON e.id = r.from_id WHERE r.to_id = ?",
            (entity_id, entity_id),
        ).fetchall()
        return Entity(
            name=erow["name"],
            type=erow["type"],
            observations=obs,
            relations=[(r["dir"], r["type"], r["other"]) for r in rels],
        )
