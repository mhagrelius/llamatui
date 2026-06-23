"""KnowledgeGraph's interface is the test surface: writing, hybrid retrieval, scoring, forget.

Runs against a throwaway SQLite file with a deterministic fake embedder — no Textual, no
server, no ``fastembed`` install (the embedder is faked, like ``FakeClock`` in test_turn.py).
"""

from llamatui.graph import KnowledgeGraph, _rrf
from llamatui.storage import connect


def _graph(tmp_path):
    return KnowledgeGraph(connect(tmp_path / "g.db"))


class FakeEmbedder:
    """Bag-of-words over a fixed vocab, with synonyms collapsed so paraphrases overlap and
    unrelated text is orthogonal (cosine 0 → below the semantic floor)."""

    VOCAB = ["adores", "spicy", "cuisine", "concise", "answer", "python"]
    SYN = {
        "likes": "adores", "loves": "adores",
        "hot": "spicy",
        "food": "cuisine", "dishes": "cuisine",
        "answers": "answer", "replies": "answer", "responses": "answer",
        "brief": "concise", "terse": "concise",
    }

    def embed(self, texts):
        out = []
        for t in texts:
            toks = [self.SYN.get(w, w) for w in t.lower().replace(",", " ").split()]
            out.append([float(toks.count(v)) for v in self.VOCAB])
        return out


# ---- writing ----------------------------------------------------------------------------
def test_observe_creates_and_dedupes(tmp_path):
    g = _graph(tmp_path)
    assert g.observe("user", "prefers concise answers").added is True
    # case-insensitive subject → same entity, identical fact deduped
    assert g.observe("USER", "prefers concise answers").added is False
    ent = g.get("user")
    assert ent is not None and ent.observations == ["prefers concise answers"]


def test_observe_links_relation(tmp_path):
    g = _graph(tmp_path)
    out = g.observe("llamatui", "written in Python", subject_type="project",
                    related_to="user", relation="created")
    assert out.added and out.related
    proj = g.get("llamatui")
    assert proj.type == "project"
    assert ("out", "created", "user") in proj.relations
    # relation defaults to "related-to"
    g.observe("a", "x", related_to="b")
    assert any(t == "related-to" for _, t, _ in g.get("a").relations)


# ---- retrieval --------------------------------------------------------------------------
def test_search_keyword_only(tmp_path):
    g = _graph(tmp_path)  # no embedder
    g.observe("llamatui", "written in Python")
    assert [e.name for e in g.search("python")] == ["llamatui"]
    assert g.search("nonexistent") == []


def test_search_semantic_paraphrase_and_floor(tmp_path):
    g = KnowledgeGraph(connect(tmp_path / "g.db"), embedder=FakeEmbedder())
    g.observe("bob", "adores spicy cuisine", subject_type="person")
    g._keyword_search("likes hot food", 10)  # sanity: lexical miss
    assert g._keyword_search("likes hot food", 10) == []
    assert [e.name for e in g.search("likes hot food")] == ["bob"]   # semantic hit
    assert g.search("quantum chromodynamics") == []                  # below floor → nothing


def test_attach_embedder_backfills(tmp_path):
    g = _graph(tmp_path)  # starts WITHOUT an embedder
    g.observe("bob", "adores spicy cuisine")
    assert g.search("likes hot food") == []          # keyword-only: paraphrase misses
    g.attach_embedder(FakeEmbedder())                # backfills the existing observation
    assert [e.name for e in g.search("likes hot food")] == ["bob"]


def test_salient_and_recent(tmp_path):
    g = _graph(tmp_path)
    g.observe("user", "fact one")
    g.observe("user", "fact two")
    g.observe("user", "x", related_to="project", relation="created")
    g.observe("project", "newest fact")
    salient = g.salient()
    assert salient[0].name == "user"                 # most observations + a relation
    assert g.recent(1)[0].content == "newest fact"   # newest first


def test_forget(tmp_path):
    g = _graph(tmp_path)
    g.observe("llamatui", "uses pytest")
    g.observe("llamatui", "uses textual")
    assert g.forget("pytest").facts == 1             # keyword delete
    assert g.forget("llamatui").entity == "llamatui"  # whole-entity delete
    assert g.get("llamatui") is None


def test_rrf_fuses_rankings():
    assert _rrf([[3, 1, 2], [1, 4]])[0] == 1
