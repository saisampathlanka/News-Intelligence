"""
Tests for upgraded recommendation engine.
Run: pytest backend/tests/test_recommendations.py -v
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.article import Article
from backend.models.relations import RelatedArticle
from backend.services.recommendations import (
    _jaccard_similarity, _parse_entities, _parse_keywords,
    _score_candidate, find_similar_articles, precompute_relationships,
    build_topic_graph, get_related_topics,
    TopicGraph, TopicEdge,
    W_TOPIC, W_ENTITY, W_KEYWORD, W_PERSP,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def make_article(db, **kwargs) -> Article:
    i = id(kwargs)
    defaults = dict(
        content_hash=f"h{i}", url=f"https://ex.com/{i}",
        title="Test Article", description="desc", content="content",
        source_name="Reuters", source_type="rss",
        published_at=datetime(2024, 4, 18, tzinfo=timezone.utc),
        is_processed=True, topic="politics",
        bias_score=0.0, sentiment_score=0.0,
        entities_json=json.dumps({"people":[], "organizations":[], "places":[]}),
        keywords_json=json.dumps([]),
    )
    a = Article(**{**defaults, **kwargs})
    db.add(a); db.commit(); db.refresh(a)
    return a


# ── Signal helpers ────────────────────────────────────────────────────────────

class TestJaccard:
    def test_identical_sets(self):
        assert _jaccard_similarity({"a","b"}, {"a","b"}) == 1.0

    def test_no_overlap(self):
        assert _jaccard_similarity({"a","b"}, {"c","d"}) == 0.0

    def test_partial_overlap(self):
        # {a,b} ∩ {b,c} = {b}, ∪ = {a,b,c}
        assert abs(_jaccard_similarity({"a","b"}, {"b","c"}) - 1/3) < 0.01

    def test_empty_set(self):
        assert _jaccard_similarity(set(), {"a"}) == 0.0
        assert _jaccard_similarity({"a"}, set()) == 0.0
        assert _jaccard_similarity(set(), set()) == 0.0

    def test_subset(self):
        # {a} ∩ {a,b,c} = {a}, ∪ = {a,b,c}
        assert abs(_jaccard_similarity({"a"}, {"a","b","c"}) - 1/3) < 0.01


class TestParseHelpers:
    def test_parse_entities_flat(self):
        ej = json.dumps({"people": ["Alice", "Bob"], "places": ["Paris"]})
        result = _parse_entities(ej)
        assert "alice" in result
        assert "bob" in result
        assert "paris" in result

    def test_parse_entities_empty(self):
        assert _parse_entities(None) == set()
        assert _parse_entities("") == set()
        assert _parse_entities(json.dumps({})) == set()

    def test_parse_entities_invalid_json(self):
        assert _parse_entities("not json") == set()

    def test_parse_keywords(self):
        kj = json.dumps(["diplomacy", "ceasefire", "nato"])
        result = _parse_keywords(kj)
        assert "diplomacy" in result
        assert "nato" in result

    def test_parse_keywords_empty(self):
        assert _parse_keywords(None) == set()
        assert _parse_keywords("") == set()


# ── Score candidate ───────────────────────────────────────────────────────────

class TestScoreCandidate:
    def test_topic_match_gives_score(self, db):
        src = make_article(db, topic="politics")
        cand = make_article(db, topic="politics")
        score, signal = _score_candidate(src, cand, set(), set())
        assert score > 0
        assert signal == "topic"

    def test_topic_mismatch_no_topic_signal(self, db):
        src = make_article(db, topic="politics")
        cand = make_article(db, topic="sports")
        score, signal = _score_candidate(src, cand, set(), set())
        # No topic match, no entities, no keywords → 0
        assert score == 0.0

    def test_entity_overlap_contributes(self, db):
        src = make_article(db, topic="politics",
                           entities_json=json.dumps({"people":["Alice","Bob"],"organizations":[],"places":[]}))
        cand = make_article(db, topic="politics",
                            entities_json=json.dumps({"people":["Alice","Charlie"],"organizations":[],"places":[]}))
        src_ents = _parse_entities(src.entities_json)
        cand_ents = {"alice", "bob"}
        score, signal = _score_candidate(src, cand, src_ents, set())
        assert score > 0

    def test_bias_diversity_bonus(self, db):
        src  = make_article(db, topic="politics", bias_score=-0.6)
        cand = make_article(db, topic="politics", bias_score=+0.6)
        score_with_bias, sig = _score_candidate(src, cand, set(), set())
        # Should get both topic (1.0) and perspective bonus
        assert score_with_bias > 0

        # Without bias difference, score should be lower
        cand2 = make_article(db, topic="politics", bias_score=-0.6)
        score_no_bias, _ = _score_candidate(src, cand2, set(), set())
        # Both get topic match but cand has perspective bonus
        assert score_with_bias >= score_no_bias

    def test_no_signals_returns_zero(self, db):
        src  = make_article(db, topic="politics")
        cand = make_article(db, topic="business")  # different topic, no overlap
        score, sig = _score_candidate(src, cand, set(), set())
        assert score == 0.0
        assert sig == "none"

    def test_score_range(self, db):
        src  = make_article(db, topic="politics", bias_score=-0.8,
                            entities_json=json.dumps({"people":["A","B","C"],"organizations":[],"places":[]}),
                            keywords_json=json.dumps(["economy","trade","sanctions"]))
        cand = make_article(db, topic="politics", bias_score=+0.8,
                            entities_json=json.dumps({"people":["A","B","D"],"organizations":[],"places":[]}),
                            keywords_json=json.dumps(["economy","trade","tariffs"]))
        src_ents = _parse_entities(src.entities_json)
        src_kws = _parse_keywords(src.keywords_json)
        score, _ = _score_candidate(src, cand, src_ents, src_kws)
        assert 0.0 <= score <= 1.0


# ── find_similar_articles ─────────────────────────────────────────────────────

class TestFindSimilar:
    def test_returns_empty_for_missing_article(self, db):
        assert find_similar_articles(db, article_id=9999) == []

    def test_returns_empty_for_unprocessed(self, db):
        a = make_article(db, is_processed=False)
        assert find_similar_articles(db, article_id=a.id) == []

    def test_returns_tuples(self, db):
        src = make_article(db, topic="politics")
        cand = make_article(db, topic="politics",
                            entities_json=json.dumps({"people":["Alice"],"organizations":[],"places":[]}),
                            keywords_json=json.dumps(["congress","legislation"]))
        results = find_similar_articles(db, src.id, limit=5)
        for r in results:
            assert len(r) == 3
            assert isinstance(r[0], Article)
            assert isinstance(r[1], float)
            assert isinstance(r[2], str)

    def test_scores_in_range(self, db):
        src = make_article(db, topic="politics")
        for i in range(5):
            make_article(db, content_hash=f"sim_{i}", url=f"https://ex.com/sim{i}",
                         topic="politics")
        results = find_similar_articles(db, src.id)
        for _, score, _ in results:
            assert 0.0 <= score <= 1.0

    def test_limit_respected(self, db):
        src = make_article(db, topic="politics")
        for i in range(10):
            make_article(db, content_hash=f"l{i}", url=f"https://ex.com/l{i}", topic="politics")
        results = find_similar_articles(db, src.id, limit=3)
        assert len(results) <= 3

    def test_uses_precomputed_if_available(self, db):
        src  = make_article(db, topic="politics")
        cand = make_article(db, topic="politics")

        # Pre-store a relationship
        db.add(RelatedArticle(
            article_id=src.id, related_id=cand.id,
            similarity_score=0.75, relation_type="entity",
        ))
        db.commit()

        results = find_similar_articles(db, src.id)
        assert len(results) == 1
        _, score, rel_type = results[0]
        assert score == 0.75
        assert rel_type == "entity"

    def test_does_not_recommend_self(self, db):
        src = make_article(db, topic="politics",
                           keywords_json=json.dumps(["policy","government","senate"]))
        results = find_similar_articles(db, src.id)
        ids = [a.id for a, _, _ in results]
        assert src.id not in ids


# ── precompute_relationships ──────────────────────────────────────────────────

class TestPrecompute:
    def test_processes_articles_without_relations(self, db):
        src  = make_article(db, topic="politics",
                            entities_json=json.dumps({"people":["A"],"organizations":[],"places":[]}))
        cand = make_article(db, topic="politics",
                            entities_json=json.dumps({"people":["A","B"],"organizations":[],"places":[]}))
        count = precompute_relationships(db, batch_size=10)
        assert count >= 1

    def test_does_not_reprocess_existing(self, db):
        src  = make_article(db, topic="politics")
        cand = make_article(db, topic="politics")
        db.add(RelatedArticle(article_id=src.id, related_id=cand.id,
                              similarity_score=0.5, relation_type="topic"))
        db.commit()

        count = precompute_relationships(db, batch_size=10)
        # src already has a relationship — skip it
        assert count == 1  # only cand gets computed

    def test_empty_db_returns_zero(self, db):
        assert precompute_relationships(db) == 0

    def test_batch_size_respected(self, db):
        for i in range(10):
            make_article(db, content_hash=f"b{i}", url=f"https://ex.com/b{i}", topic="politics")
        count = precompute_relationships(db, batch_size=3)
        assert count == 3


# ── Topic Graph ───────────────────────────────────────────────────────────────

class TestTopicGraph:
    def _seed(self, db, topic_kw_pairs: list):
        for i, (topic, kws) in enumerate(topic_kw_pairs):
            make_article(db, content_hash=f"tg{i}", url=f"https://ex.com/tg{i}",
                         topic=topic, keywords_json=json.dumps(kws))

    def test_empty_db_returns_empty_graph(self, db):
        g = build_topic_graph(db)
        assert g.nodes == []
        assert g.edges == []
        assert g.total_articles == 0

    def test_nodes_contain_all_topics(self, db):
        self._seed(db, [
            ("politics", ["congress", "senate"]),
            ("business", ["market", "stocks"]),
            ("technology", ["ai", "software"]),
        ])
        g = build_topic_graph(db)
        assert "politics" in g.nodes
        assert "business" in g.nodes
        assert "technology" in g.nodes

    def test_shared_keywords_create_edge(self, db):
        self._seed(db, [
            ("politics",   ["economy", "trade", "policy"]),
            ("business",   ["economy", "trade", "market"]),  # shares economy+trade
            ("technology", ["software", "ai", "algorithm"]),  # no overlap
        ])
        g = build_topic_graph(db)
        edge_pairs = {(e.topic_a, e.topic_b) for e in g.edges}
        # politics ↔ business should have an edge (share economy, trade)
        assert ("business", "politics") in edge_pairs or ("politics", "business") in edge_pairs

    def test_no_shared_keywords_no_edge(self, db):
        self._seed(db, [
            ("sports",  ["football", "soccer", "championship"]),
            ("science", ["quantum", "physics", "biology"]),
        ])
        g = build_topic_graph(db)
        # No shared keywords → no edge
        assert len(g.edges) == 0

    def test_edge_weight_positive(self, db):
        self._seed(db, [
            ("politics",  ["economy", "trade"]),
            ("business",  ["economy", "market"]),
        ])
        g = build_topic_graph(db)
        for e in g.edges:
            assert e.weight > 0
            assert e.co_count > 0

    def test_to_dict_serializable(self, db):
        import json as _json
        self._seed(db, [
            ("politics", ["economy"]),
            ("business", ["economy"]),
        ])
        g = build_topic_graph(db)
        d = g.to_dict()
        _json.dumps(d)  # must not raise
        assert "nodes" in d
        assert "edges" in d
        assert "total_articles" in d

    def test_neighbors_returns_related(self, db):
        self._seed(db, [
            ("politics",  ["economy", "trade", "congress"]),
            ("business",  ["economy", "trade", "market"]),
            ("technology",["economy", "software", "ai"]),
        ])
        g = build_topic_graph(db)
        neighbors = g.neighbors("politics", top_n=5)
        # Should find business and/or technology as neighbors
        neighbor_topics = [t for t, _ in neighbors]
        assert len(neighbor_topics) >= 1

    def test_neighbors_sorted_by_weight(self, db):
        self._seed(db, [
            ("politics",  ["economy","trade","policy","congress","senate"]),
            ("business",  ["economy","trade","policy","market"]),   # 3 shared
            ("technology",["economy","ai"]),                          # 1 shared
        ])
        g = build_topic_graph(db)
        neighbors = g.neighbors("politics")
        if len(neighbors) >= 2:
            weights = [w for _, w in neighbors]
            assert weights == sorted(weights, reverse=True)


class TestGetRelatedTopics:
    def test_returns_list(self, db):
        for i, topic in enumerate(["politics", "business", "technology"]):
            make_article(db, content_hash=f"rt{i}", url=f"https://ex.com/rt{i}",
                         topic=topic, keywords_json=json.dumps(["economy","trade"]))
        result = get_related_topics(db, "politics", top_n=5)
        assert isinstance(result, list)

    def test_fallback_when_no_edges(self, db):
        make_article(db, topic="politics", keywords_json=json.dumps(["congress"]))
        make_article(db, content_hash="h2", url="https://ex.com/2",
                     topic="sports", keywords_json=json.dumps(["football"]))
        result = get_related_topics(db, "politics", top_n=5)
        # Should fallback to volume-based
        assert isinstance(result, list)
        for r in result:
            assert "topic" in r

    def test_empty_db_returns_empty(self, db):
        result = get_related_topics(db, "politics")
        assert result == []

    def test_result_has_required_keys(self, db):
        for i, topic in enumerate(["politics","business"]):
            make_article(db, content_hash=f"rk{i}", url=f"https://ex.com/rk{i}",
                         topic=topic, keywords_json=json.dumps(["economy","trade","market"]))
        result = get_related_topics(db, "politics")
        for r in result:
            assert "topic" in r
            assert "weight" in r
            assert "relation" in r

    def test_own_topic_not_in_results(self, db):
        for i in range(3):
            make_article(db, content_hash=f"ot{i}", url=f"https://ex.com/ot{i}",
                         topic="politics", keywords_json=json.dumps(["economy"]))
        result = get_related_topics(db, "politics")
        topics = [r["topic"] for r in result]
        assert "politics" not in topics


# ── Weight constant validation ────────────────────────────────────────────────

class TestWeightConstants:
    def test_weights_positive(self):
        assert W_TOPIC > 0
        assert W_ENTITY > 0
        assert W_KEYWORD > 0
        assert W_PERSP > 0

    def test_topic_is_highest_weight(self):
        assert W_TOPIC >= W_ENTITY
        assert W_TOPIC >= W_KEYWORD
        assert W_TOPIC >= W_PERSP

    def test_weights_sum_to_one(self):
        total = W_TOPIC + W_ENTITY + W_KEYWORD + W_PERSP
        assert abs(total - 1.0) < 0.001
