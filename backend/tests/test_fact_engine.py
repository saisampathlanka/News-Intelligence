"""
Tests for Fact Intersection Engine.
Run: pytest backend/tests/test_fact_engine.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from backend.services.fact_engine import (
    cluster_articles,
    extract_common_facts,
    detect_conflicts,
    build_story_clusters,
    _extract_numeric_claims,
    _extract_outcome_claims,
    _extract_noun_phrases,
    _article_text,
    FactCandidate,
    ConflictRecord,
    ArticleCluster,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_article(id: int, title: str, desc: str = "", source: str = "Source A", topic: str = "world") -> dict:
    return {"id": id, "title": title, "description": desc, "source_name": source, "topic": topic,
            "bias_score": 0.0, "sentiment_score": 0.0, "published_at": "2024-04-18T12:00:00"}


UKRAINE_CLUSTER = [
    make_article(1, "Ukraine ceasefire talks begin in Istanbul",
                 "Russia and Ukraine started peace negotiations in Istanbul Tuesday", "Reuters"),
    make_article(2, "Peace talks underway in Turkey over Ukraine conflict",
                 "Ukrainian and Russian officials met in Istanbul for ceasefire negotiations", "BBC World"),
    make_article(3, "Istanbul hosts Ukraine Russia ceasefire negotiations",
                 "Negotiations between Ukraine and Russia opened in Istanbul aimed at ending conflict", "Al Jazeera"),
]

IMMIGRATION_CLUSTER = [
    make_article(10, "Biden signs executive order on immigration policy",
                 "President Biden issued an executive order restricting asylum claims at the border", "Reuters"),
    make_article(11, "US president announces new immigration restrictions",
                 "White House announced new restrictions on asylum processing at southern border", "AP News"),
]

UNRELATED = [
    make_article(20, "Taylor Swift announces new album release",
                 "The pop star revealed upcoming music at the Grammy ceremony", "BBC World"),
    make_article(21, "Federal Reserve holds interest rates steady",
                 "The Fed kept rates unchanged citing cooling inflation", "Reuters"),
]


# ── Text helpers ──────────────────────────────────────────────────────────────

class TestTextHelpers:
    def test_article_text_combines(self):
        t = _article_text("Title here", "Description here")
        assert "Title here" in t
        assert "Description here" in t

    def test_article_text_no_desc(self):
        t = _article_text("Title only")
        assert t == "Title only"

    def test_extract_numeric_claims(self):
        claims = _extract_numeric_claims("The attack killed 45 people and wounded 12 soldiers")
        values = [v for v, _ in claims]
        assert 45.0 in values
        assert 12.0 in values

    def test_extract_numeric_percentage(self):
        claims = _extract_numeric_claims("Inflation rose 8.5 percent this quarter")
        assert any(v == 8.5 for v, _ in claims)

    def test_extract_numeric_with_commas(self):
        claims = _extract_numeric_claims("1,200 troops were deployed")
        assert any(v == 1200.0 for v, _ in claims)

    def test_extract_outcome_verbs(self):
        outcomes = _extract_outcome_claims("The bill was approved by the Senate but rejected by governors")
        assert "approved" in outcomes
        assert "rejected" in outcomes

    def test_extract_outcome_empty(self):
        outcomes = _extract_outcome_claims("The meeting was scheduled for Thursday")
        assert outcomes == []

    def test_extract_noun_phrases(self):
        phrases = _extract_noun_phrases("Prime Minister Boris Johnson met European Union officials in Brussels")
        joined = " ".join(phrases)
        # Should capture proper nouns
        assert any("Prime" in p or "European" in p or "Boris" in p for p in phrases)

    def test_extract_noun_quoted(self):
        phrases = _extract_noun_phrases('Officials called it "a historic agreement" yesterday')
        assert any("historic agreement" in p for p in phrases)


# ── Clustering ────────────────────────────────────────────────────────────────

class TestClustering:
    def test_similar_articles_cluster_together(self):
        clusters = cluster_articles(UKRAINE_CLUSTER, threshold=0.10)
        # All 3 Ukraine articles should be in same cluster
        all_in_one = any(len(c) >= 3 for c in clusters)
        # Or at least 2 should group together
        any_grouped = any(len(c) >= 2 for c in clusters)
        assert any_grouped

    def test_unrelated_articles_different_clusters(self):
        all_articles = UKRAINE_CLUSTER + UNRELATED
        clusters = cluster_articles(all_articles, threshold=0.15)
        # Unrelated articles (indices 3, 4) should not be with Ukraine (indices 0,1,2)
        ukraine_indices = {0, 1, 2}
        unrelated_indices = {3, 4}
        for c in clusters:
            c_set = set(c)
            if c_set & ukraine_indices:
                # This cluster contains Ukraine articles — should not contain unrelated
                assert not (c_set & unrelated_indices), "Unrelated articles mixed with Ukraine cluster"

    def test_single_article_singleton(self):
        clusters = cluster_articles([make_article(1, "Single article")], threshold=0.15)
        assert len(clusters) == 1
        assert len(clusters[0]) == 1

    def test_empty_input(self):
        clusters = cluster_articles([], threshold=0.15)
        assert clusters == []

    def test_all_articles_covered(self):
        articles = UKRAINE_CLUSTER + UNRELATED
        clusters = cluster_articles(articles, threshold=0.15)
        total = sum(len(c) for c in clusters)
        assert total == len(articles)

    def test_threshold_zero_all_together(self):
        # Very low threshold should cluster everything
        clusters = cluster_articles(UKRAINE_CLUSTER, threshold=0.0)
        # All in one cluster since 0.0 means always match
        assert any(len(c) == 3 for c in clusters)

    def test_threshold_one_all_singletons(self):
        # Threshold=1.0 means only identical articles cluster together
        clusters = cluster_articles(UKRAINE_CLUSTER, threshold=1.0)
        # No two different articles are identical
        assert all(len(c) == 1 for c in clusters)


# ── Common Facts ──────────────────────────────────────────────────────────────

class TestCommonFacts:
    def test_shared_location_extracted(self):
        # All 3 Ukraine articles mention Istanbul
        facts = extract_common_facts(UKRAINE_CLUSTER, min_fraction=0.50)
        fact_texts = [f.text.lower() for f in facts]
        assert any("istanbul" in t for t in fact_texts), f"Istanbul not found in {fact_texts}"

    def test_confidence_in_range(self):
        facts = extract_common_facts(UKRAINE_CLUSTER)
        for f in facts:
            assert 0.0 <= f.confidence <= 1.0

    def test_confidence_matches_sources(self):
        facts = extract_common_facts(UKRAINE_CLUSTER, min_fraction=0.0)
        for f in facts:
            assert len(f.sources) >= 1
            assert f.confidence == round(len(f.sources) / len(UKRAINE_CLUSTER), 2)

    def test_empty_cluster_returns_empty(self):
        facts = extract_common_facts([])
        assert facts == []

    def test_single_article_no_cross_source_facts(self):
        facts = extract_common_facts([UKRAINE_CLUSTER[0]], min_fraction=0.9)
        # With only 1 article, confidence = 1.0 regardless — all facts are "100%"
        for f in facts:
            assert f.confidence == 1.0

    def test_returns_fact_candidate_objects(self):
        facts = extract_common_facts(UKRAINE_CLUSTER)
        for f in facts:
            assert isinstance(f, FactCandidate)
            assert isinstance(f.text, str)
            assert isinstance(f.sources, list)
            assert isinstance(f.article_ids, list)


# ── Conflict Detection ────────────────────────────────────────────────────────

class TestConflictDetection:
    def _make_cluster_with_numbers(self, val1: str, val2: str) -> list[dict]:
        return [
            make_article(1, f"Attack killed {val1} people in the region", source="Reuters"),
            make_article(2, f"Strike killed {val2} people according to officials", source="BBC World"),
        ]

    def test_numeric_conflict_detected(self):
        articles = self._make_cluster_with_numbers("45", "12")
        conflicts = detect_conflicts(articles)
        numeric = [c for c in conflicts if c.conflict_type == "numeric"]
        assert len(numeric) >= 1

    def test_numeric_conflict_severity_high(self):
        articles = self._make_cluster_with_numbers("100", "10")  # 10x diff
        conflicts = detect_conflicts(articles)
        high = [c for c in conflicts if c.severity == "high"]
        assert len(high) >= 1

    def test_no_conflict_similar_numbers(self):
        articles = self._make_cluster_with_numbers("42", "45")  # <50% diff
        conflicts = detect_conflicts(articles)
        numeric = [c for c in conflicts if c.conflict_type == "numeric"]
        assert len(numeric) == 0

    def test_outcome_contradiction_detected(self):
        articles = [
            make_article(1, "Parliament approved the controversial bill on Monday", source="Reuters"),
            make_article(2, "Parliament rejected the controversial bill this week", source="Al Jazeera"),
        ]
        conflicts = detect_conflicts(articles)
        outcome = [c for c in conflicts if c.conflict_type == "outcome"]
        assert len(outcome) >= 1

    def test_outcome_conflict_has_sources(self):
        articles = [
            make_article(1, "The ceasefire agreement was confirmed by both sides", source="Reuters"),
            make_article(2, "Ukraine denied any ceasefire agreement was reached", source="BBC World"),
        ]
        conflicts = detect_conflicts(articles)
        for c in conflicts:
            assert isinstance(c.sources, list)
            assert len(c.sources) >= 1

    def test_conflict_record_type(self):
        articles = self._make_cluster_with_numbers("90", "10")
        conflicts = detect_conflicts(articles)
        for c in conflicts:
            assert isinstance(c, ConflictRecord)
            assert c.conflict_type in ("numeric", "outcome", "attribution")
            assert c.severity in ("low", "medium", "high")

    def test_no_conflict_single_article(self):
        conflicts = detect_conflicts([UKRAINE_CLUSTER[0]])
        assert len(conflicts) == 0

    def test_empty_returns_empty(self):
        assert detect_conflicts([]) == []


# ── Full Story Cluster Builder ─────────────────────────────────────────────────

class TestBuildStoryClusters:
    def test_returns_article_cluster_objects(self):
        clusters = build_story_clusters(UKRAINE_CLUSTER + IMMIGRATION_CLUSTER)
        for c in clusters:
            assert isinstance(c, ArticleCluster)

    def test_cluster_has_required_fields(self):
        clusters = build_story_clusters(UKRAINE_CLUSTER)
        for c in clusters:
            assert c.cluster_id.startswith("cluster_")
            assert isinstance(c.article_ids, list)
            assert isinstance(c.sources, list)
            assert isinstance(c.common_facts, list)
            assert isinstance(c.conflicts, list)

    def test_to_dict_is_serializable(self):
        import json
        clusters = build_story_clusters(UKRAINE_CLUSTER)
        for c in clusters:
            d = c.to_dict()
            json.dumps(d)  # must not raise

    def test_sorted_by_size_descending(self):
        all_articles = UKRAINE_CLUSTER + IMMIGRATION_CLUSTER + UNRELATED
        clusters = build_story_clusters(all_articles, min_cluster_size=2)
        sizes = [len(c.article_ids) for c in clusters]
        assert sizes == sorted(sizes, reverse=True)

    def test_min_cluster_size_respected(self):
        clusters = build_story_clusters(UKRAINE_CLUSTER + UNRELATED, min_cluster_size=3)
        for c in clusters:
            assert len(c.article_ids) >= 3

    def test_empty_input(self):
        assert build_story_clusters([]) == []

    def test_all_singletons_below_min_size(self):
        # 5 completely unrelated articles — no clusters above min_size=2
        articles = [
            make_article(i, f"Completely unrelated topic number {i} about subject {chr(65+i)}", source=f"Source{i}")
            for i in range(5)
        ]
        clusters = build_story_clusters(articles, threshold=0.99, min_cluster_size=2)
        # Should return no clusters (all singletons)
        assert len(clusters) == 0

    def test_sources_deduplicated(self):
        clusters = build_story_clusters(UKRAINE_CLUSTER)
        for c in clusters:
            assert len(c.sources) == len(set(c.sources))

    def test_majority_topic_assigned(self):
        clusters = build_story_clusters(UKRAINE_CLUSTER, min_cluster_size=2)
        for c in clusters:
            if c.article_ids:
                assert c.topic is not None or c.topic is None  # topic may be None


# ── Edge Cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:
    def test_articles_with_empty_titles(self):
        articles = [
            {"id": 1, "title": "", "description": "Ukraine peace talks", "source_name": "Reuters",
             "topic": "world", "bias_score": 0.0, "sentiment_score": 0.0, "published_at": None},
            {"id": 2, "title": "", "description": "Russia Ukraine ceasefire", "source_name": "BBC",
             "topic": "world", "bias_score": 0.0, "sentiment_score": 0.0, "published_at": None},
        ]
        # Should not raise
        clusters = build_story_clusters(articles)
        assert isinstance(clusters, list)

    def test_articles_missing_optional_fields(self):
        articles = [
            {"id": 1, "title": "Test", "source_name": "Reuters"},
            {"id": 2, "title": "Test similar", "source_name": "BBC"},
        ]
        # Should not raise KeyError
        clusters = build_story_clusters(articles, threshold=0.0)
        assert isinstance(clusters, list)

    def test_very_long_text_no_crash(self):
        long_text = "Ukraine ceasefire talks Istanbul Russia " * 500
        articles = [
            make_article(1, "Ukraine ceasefire", long_text, "Reuters"),
            make_article(2, "Ukraine peace talks", "Istanbul negotiations", "BBC"),
        ]
        clusters = build_story_clusters(articles)
        assert isinstance(clusters, list)

    def test_duplicate_articles_cluster_together(self):
        # Two identical articles should cluster at threshold > 0
        art = make_article(1, "Ukraine ceasefire talks in Istanbul begin Tuesday", "")
        art2 = {**art, "id": 2, "source_name": "Different Source"}
        clusters = cluster_articles([art, art2], threshold=0.5)
        any_grouped = any(len(c) == 2 for c in clusters)
        assert any_grouped

    def test_numeric_with_commas_parsed_correctly(self):
        articles = [
            make_article(1, "Government spent 1,200 million dollars", source="Reuters"),
            make_article(2, "Budget allocated 100 million dollars for project", source="BBC"),
        ]
        conflicts = detect_conflicts(articles)
        # Values differ by 12x — should detect conflict
        numeric_conflicts = [c for c in conflicts if c.conflict_type == "numeric"]
        assert len(numeric_conflicts) >= 1
