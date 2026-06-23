"""The memory *surface* — turns the knowledge graph into something the model can use.

This is a thin wrapper over :class:`~llamatui.graph.KnowledgeGraph`. It owns two things and
nothing else:

* **Tools** the model calls — :meth:`remember`, :meth:`recall`, :meth:`forget`, exposed as
  Agent Framework ``FunctionTool``s. Relations are folded into ``remember`` (a target +
  relation) rather than a separate tool, so a local model has fewer tools to juggle.
* **The ambient preamble** (:meth:`preamble`) spliced into the system prompt: a curated
  **Background** (salient entities) plus **Recently learned** (fresh observations).

All storage, indexing, scoring, embedding, and hybrid retrieval live in the graph. Memory just
phrases intents and renders results — so its tests read like a conversation with the model, and
the graph's tests cover the mechanics.
"""

from typing import Annotated

from agent_framework import FunctionTool

from .graph import Embedder, Entity, KnowledgeGraph

# Preamble budgets — keep the block small so the cacheable system-prompt prefix stays stable.
_BG_ENTITIES = 8
_BG_OBS_EACH = 3
_RECENT_LINES = 6
_LINE_CAP = 160
_RECALL_ENTITIES = 5


def _truncate(text: str, cap: int = _LINE_CAP) -> str:
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _rel_text(rel: tuple[str, str, str]) -> str:
    """Render a (direction, type, other) relation: 'out' points away, 'in' points in."""
    direction, type_, other = rel
    return f"{type_} → {other}" if direction == "out" else f"{other} → {type_}"


class Memory:
    def __init__(self, graph: KnowledgeGraph) -> None:
        self.graph = graph

    def attach_embedder(self, embedder: Embedder) -> None:
        """Enable semantic recall: hand the loaded embedder to the graph (it backfills vectors).

        Public so the App's adapter never reaches into internals. Call on the main thread."""
        self.graph.attach_embedder(embedder)

    # ---- tools (model-facing) -----------------------------------------------------------
    def remember(
        self,
        content: Annotated[str, "The fact to store, as a short factual statement."],
        subject: Annotated[
            str, "Who/what the fact is about (an entity name). Defaults to the user."
        ] = "user",
        subject_type: Annotated[
            str | None,
            "Optional kind of the subject: person, project, preference, concept, tool, place…",
        ] = None,
        related_to: Annotated[
            str | None,
            "Optional other entity to link the subject to (creates a relationship).",
        ] = None,
        relation: Annotated[
            str | None,
            "How subject relates to related_to, e.g. uses / knows / depends-on / part-of.",
        ] = None,
    ) -> str:
        """Save a durable fact about the user or their world so it persists across
        conversations. Optionally link the subject to another entity."""
        outcome = self.graph.observe(
            subject, content, subject_type=subject_type, related_to=related_to, relation=relation
        )
        note = f" ({subject} {(relation or 'related-to').strip()} {related_to})" if outcome.related else ""
        if not outcome.added and not outcome.related:
            return f"Already knew that about {subject}."
        return f"Noted about {subject}.{note}"

    def recall(
        self,
        query: Annotated[str, "What to look up in memory (keywords or a natural-language ask)."],
    ) -> str:
        """Search your persistent memory for what you know about the user or a topic."""
        entities = self.graph.search(query, _RECALL_ENTITIES)
        if not entities:
            return f"No memories found for “{query}”."
        return "\n".join(self._render_entity(e) for e in entities)

    def forget(
        self,
        query: Annotated[str, "An entity name to delete, or keywords whose facts to remove."],
    ) -> str:
        """Delete memories. Pass an exact entity name to forget it entirely, or keywords to
        remove matching individual facts."""
        result = self.graph.forget(query)
        if result.entity is not None:
            return f"Forgot everything about {result.entity}."
        return (
            f"Forgot {result.facts} fact(s) matching “{query}”."
            if result.facts
            else f"Nothing to forget for “{query}”."
        )

    def build_tools(self) -> list[FunctionTool]:
        """The model-facing tools (bound to this instance's graph)."""
        return [
            FunctionTool(
                func=self.remember,
                name="remember",
                description=(
                    "Save a durable fact about the user or their world to persistent memory "
                    "(optionally linking two things). Use when lasting information emerges."
                ),
            ),
            FunctionTool(
                func=self.recall,
                name="recall",
                description="Search persistent memory for what you know about the user or a topic.",
            ),
            FunctionTool(
                func=self.forget,
                name="forget",
                description="Delete memories by entity name or by keywords.",
            ),
        ]

    # ---- ambient preamble ---------------------------------------------------------------
    def preamble(self) -> str | None:
        """A compact Background (salient) + Recently-learned (fresh) block, or None if empty."""
        background = [e for e in self.graph.salient(_BG_ENTITIES) if e.observations or e.relations]
        # Pin the principal ("user") first when present.
        background.sort(key=lambda e: e.name.lower() != "user")

        shown: set[str] = set()
        bg_lines: list[str] = []
        for e in background:
            obs = e.observations[:_BG_OBS_EACH]
            shown.update(obs)
            head = e.name + (f" ({e.type})" if e.type else "")
            parts = []
            if obs:
                parts.append("; ".join(obs))
            if e.relations:
                parts.append(", ".join(_rel_text(r) for r in e.relations[:_BG_OBS_EACH]))
            bg_lines.append(_truncate(f"- {head}: " + " · ".join(parts)))

        recent_lines: list[str] = []
        for r in self.graph.recent(_RECENT_LINES * 3):
            if r.content in shown:
                continue
            recent_lines.append(_truncate(f"- [{r.entity}] {r.content}"))
            if len(recent_lines) >= _RECENT_LINES:
                break

        if not bg_lines and not recent_lines:
            return None

        sections = ["What you remember about the user and their world:"]
        if bg_lines:
            sections.append("Background:\n" + "\n".join(bg_lines))
        if recent_lines:
            sections.append("Recently learned:\n" + "\n".join(recent_lines))
        return "\n\n".join(sections)

    # ---- rendering ----------------------------------------------------------------------
    def _render_entity(self, e: Entity) -> str:
        head = e.name + (f" ({e.type})" if e.type else "")
        lines = [f"{head}:"]
        for o in e.observations:
            lines.append(f"  - {o}")
        for r in e.relations:
            lines.append(f"  · {_rel_text(r)}")
        return "\n".join(lines)
