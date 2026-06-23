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


def test_search_returns_only_matched_observations(tmp_path):
    g = _graph(tmp_path)
    # An entity loaded with many facts must not dump its whole profile on a narrow query.
    g.observe("user", "plays Destiny 2")
    for i in range(10):
        g.observe("user", f"unrelated work fact {i}")
    hit = g.search("Destiny")[0]
    assert hit.name == "user"
    assert hit.observations == ["plays Destiny 2"]   # just the match, not all 11 facts


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


# ---- pinning ----------------------------------------------------------------------------
def test_pin_via_observe(tmp_path):
    g = _graph(tmp_path)
    g.observe("user", "avoids chicken eggs", pin=True)
    g.observe("user", "likes blue")  # not pinned
    assert [p.content for p in g.pinned()] == ["avoids chicken eggs"]


def test_pin_existing_by_substring(tmp_path):
    g = _graph(tmp_path)
    g.observe("user", "avoids sunflower oil")
    g.observe("user", "plays games")
    assert g.pin("user", "sunflower") == 1
    assert [p.content for p in g.pinned()] == ["avoids sunflower oil"]
    assert g.pin("nobody") == 0          # unknown entity


def test_pinned_orders_salient_entity_first(tmp_path):
    g = _graph(tmp_path)
    g.observe("condition", "minor note", pin=True)
    for i in range(3):
        g.observe("user", f"fact {i}")    # make user more salient
    g.observe("user", "core user fact", pin=True)
    # user (more observations) outranks the condition entity
    assert g.pinned()[0].entity == "user"
