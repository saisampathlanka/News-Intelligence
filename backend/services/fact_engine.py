"""
Fact Intersection Engine
========================
Groups articles covering the same story, then extracts:
  - Common facts   : claims corroborated by >= min_sources outlets
  - Conflicting claims : same entity/number reported differently across sources

Pipeline:
  1. TF-IDF vectorize article text (title + description)
  2. Cosine similarity → cluster articles above SIMILARITY_THRESHOLD
  3. Within each cluster, extract shared vs. divergent facts

Design notes:
  - No GPU or model server required — pure sklearn + stdlib
  - Clusters are computed on-demand or pre-cached (scheduler-triggered)
  - "Common fact" = phrase/entity appearing in >= 60% of cluster articles
  - "Conflict"   = numeric divergence >50% OR outcome verb contradiction
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger("news_intel.fact_engine")

# ── Configuration ─────────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD  = 0.18   # min cosine sim to group articles into a cluster
MIN_CLUSTER_SIZE      = 2      # need at least 2 articles to form a cluster
COMMON_FACT_THRESHOLD = 0.55   # fraction of cluster articles that must share a fact
NUMERIC_CONFLICT_RATIO = 1.50  # >50% divergence in same-context numbers = conflict

# Numeric context patterns — capture value + context word
_NUM_CONTEXTS = re.compile(
    r'(\d[\d,\.]*)\s*(?:percent|%|million|billion|trillion|thousand|'
    r'people|soldiers|troops|casualties|dead|killed|wounded|injured|'
    r'votes|seats|years|months|days|hours)',
    re.IGNORECASE,
)

# Outcome verb patterns — detect contradictory claims
_OUTCOME_VERBS = re.compile(
    r'\b(won|lost|defeated|failed|succeeded|collapsed|rejected|approved|'
    r'passed|blocked|confirmed|denied|admitted|refused|agreed|disagreed|'
    r'condemned|praised|supported|opposed)\b',
    re.IGNORECASE,
)


# ── Data Structures ────────────────────────────────────────────────────────────

@dataclass
class ArticleCluster:
    cluster_id: str
    article_ids: list[int]
    article_titles: list[str]
    sources: list[str]
    topic: Optional[str]
    similarity_scores: list[float]  # pairwise max sim within cluster
    common_facts: list[dict]        # corroborated claims
    conflicts: list[dict]           # divergent claims
    coverage_start: Optional[str]
    coverage_end: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FactCandidate:
    text: str
    sources: list[str]
    article_ids: list[int]
    confidence: float  # fraction of cluster articles that mention it


@dataclass
class ConflictRecord:
    conflict_type: str        # numeric | outcome | attribution
    description: str
    values: list[str]         # the diverging values
    sources: list[str]        # sources on each side
    article_ids: list[int]
    severity: str             # low | medium | high


# ── Text Helpers ──────────────────────────────────────────────────────────────

def _article_text(title: str, description: str = "") -> str:
    """Combine fields into a single string for vectorization."""
    parts = [title or ""]
    if description:
        parts.append(description)
    return " ".join(parts).strip()


def _normalize_num(s: str) -> float:
    """Parse '1,234.5' → 1234.5"""
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return 0.0


def _extract_numeric_claims(text: str) -> list[tuple[float, str]]:
    """Return list of (value, full_match_string) from text."""
    return [
        (_normalize_num(m.group(1)), m.group(0))
        for m in _NUM_CONTEXTS.finditer(text)
    ]


def _extract_outcome_claims(text: str) -> list[str]:
    """Return list of outcome verb matches."""
    return [m.group(0).lower() for m in _OUTCOME_VERBS.finditer(text)]


# ── Clustering ────────────────────────────────────────────────────────────────

def cluster_articles(articles: list[dict], threshold: float = SIMILARITY_THRESHOLD) -> list[list[int]]:
    """
    Group articles by text similarity using TF-IDF + cosine.

    Args:
        articles: list of dicts with keys: id, title, description
        threshold: cosine similarity cutoff to join a cluster

    Returns:
        List of clusters, each being a list of article indices (NOT ids)
    """
    if len(articles) < MIN_CLUSTER_SIZE:
        return [[i] for i in range(len(articles))]

    texts = [
        _article_text(a.get("title", ""), a.get("description", ""))
        for a in articles
    ]

    # Vectorize — ngram (1,2) captures 'ceasefire talks', 'executive order' etc.
    vec = TfidfVectorizer(
        stop_words="english",
        max_features=10_000,
        ngram_range=(1, 2),
        min_df=1,
        sublinear_tf=True,  # log normalization reduces dominance of frequent terms
    )
    try:
        matrix = vec.fit_transform(texts)
    except ValueError:
        # Empty vocabulary (all stop words) — return singletons
        logger.warning("TF-IDF: empty vocabulary, returning singletons")
        return [[i] for i in range(len(articles))]

    sim_matrix = cosine_similarity(matrix)

    # Greedy single-linkage clustering
    visited = set()
    clusters: list[list[int]] = []

    for i in range(len(articles)):
        if i in visited:
            continue
        cluster = [i]
        visited.add(i)
        for j in range(i + 1, len(articles)):
            if j not in visited and sim_matrix[i, j] >= threshold:
                cluster.append(j)
                visited.add(j)
        clusters.append(cluster)

    return clusters


# ── Common Fact Extraction ────────────────────────────────────────────────────

def _extract_noun_phrases(text: str) -> list[str]:
    """
    Lightweight noun-phrase extraction without spaCy.
    Captures: proper nouns (Capitalized Words), quoted phrases, numbers-with-context.
    """
    phrases = []

    # Multi-word proper nouns: "Prime Minister", "United States"
    proper = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b', text)
    phrases.extend([p.strip() for p in proper if len(p) > 3])

    # Single proper nouns >= 6 chars: Istanbul, Ukraine, Reuters
    _skip = {'According', 'However', 'Meanwhile', 'Despite', 'Following',
             'President', 'Minister', 'Official', 'Government', 'Officials',
             'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday', 'Monday'}
    single = re.findall(r'\b[A-Z][a-z]{5,}\b', text)
    phrases.extend([p for p in single if p not in _skip])

    # Quoted phrases (strong factual claims)
    quoted = re.findall(r'"([^"]{5,80})"', text)
    phrases.extend(quoted)

    # Numeric claims with context
    for _, match_str in _extract_numeric_claims(text):
        phrases.append(match_str.strip())

    return phrases


def extract_common_facts(
    cluster_articles: list[dict],
    min_fraction: float = COMMON_FACT_THRESHOLD,
) -> list[FactCandidate]:
    """
    Find facts mentioned in >= min_fraction of cluster articles.

    Strategy:
      1. Extract candidate phrases from each article
      2. Normalize to lowercase for matching
      3. Count across-source mentions
      4. Return candidates exceeding the threshold
    """
    n = len(cluster_articles)
    if n == 0:
        return []

    # phrase_lower -> {sources, article_ids}
    phrase_registry: dict[str, dict] = defaultdict(lambda: {"sources": set(), "ids": set(), "original": ""})

    for art in cluster_articles:
        text = _article_text(art.get("title", ""), art.get("description", ""))
        phrases = _extract_noun_phrases(text)
        source = art.get("source_name", "unknown")
        art_id = art.get("id", 0)

        for phrase in phrases:
            key = phrase.lower().strip()
            if len(key) < 4:
                continue
            pr = phrase_registry[key]
            pr["sources"].add(source)
            pr["ids"].add(art_id)
            if not pr["original"]:
                pr["original"] = phrase

    # Filter to phrases mentioned by enough sources
    common = []
    for key, data in phrase_registry.items():
        frac = len(data["sources"]) / n
        if frac >= min_fraction:
            common.append(FactCandidate(
                text=data["original"] or key,
                sources=sorted(data["sources"]),
                article_ids=sorted(data["ids"]),
                confidence=round(frac, 2),
            ))

    # Sort by confidence desc
    return sorted(common, key=lambda x: x.confidence, reverse=True)[:20]


# ── Conflict Detection ────────────────────────────────────────────────────────

def detect_conflicts(cluster_articles: list[dict]) -> list[ConflictRecord]:
    """
    Detect divergent claims across articles in a cluster.

    Checks:
      1. Numeric divergence: same context, values differing by >50%
      2. Outcome contradictions: directly opposing verbs (won/lost, approved/rejected)
    """
    conflicts: list[ConflictRecord] = []

    # ── Numeric conflict ──────────────────────────────────────────────────────
    # Group numeric claims by their unit/context word
    context_values: dict[str, list[tuple[float, str, int]]] = defaultdict(list)

    for art in cluster_articles:
        text = _article_text(art.get("title", ""), art.get("description", ""))
        art_id = art.get("id", 0)
        source = art.get("source_name", "unknown")

        for value, full_match in _extract_numeric_claims(text):
            # Key = context word (the unit after the number)
            context_word = re.sub(r'[\d,\.\s]', '', full_match).strip().lower()
            if context_word:
                context_values[context_word].append((value, source, art_id))

    for context, entries in context_values.items():
        if len(entries) < 2:
            continue
        values = [v for v, _, _ in entries]
        min_v, max_v = min(values), max(values)
        if min_v <= 0:
            continue
        ratio = max_v / min_v
        if ratio >= NUMERIC_CONFLICT_RATIO:
            severity = "high" if ratio > 3.0 else ("medium" if ratio > 2.0 else "low")
            conflicts.append(ConflictRecord(
                conflict_type="numeric",
                description=f"Divergent {context} count: {min_v} vs {max_v} ({ratio:.1f}x difference)",
                values=[f"{v} {context}" for v, _, _ in entries],
                sources=[s for _, s, _ in entries],
                article_ids=[i for _, _, i in entries],
                severity=severity,
            ))

    # ── Outcome contradiction ─────────────────────────────────────────────────
    ANTONYMS: list[tuple[set, set]] = [
        ({"won", "succeeded", "approved", "passed", "confirmed", "agreed"},
         {"lost", "failed", "rejected", "blocked", "denied", "disagreed"}),
        ({"praised", "supported", "endorsed"},
         {"condemned", "opposed", "criticized"}),
    ]

    all_outcomes: list[tuple[str, str, int]] = []  # (verb, source, id)
    for art in cluster_articles:
        text = _article_text(art.get("title", ""), art.get("description", ""))
        for verb in _extract_outcome_claims(text):
            all_outcomes.append((verb, art.get("source_name", "unknown"), art.get("id", 0)))

    for positive_set, negative_set in ANTONYMS:
        pos_hits = [(v, s, i) for v, s, i in all_outcomes if v in positive_set]
        neg_hits = [(v, s, i) for v, s, i in all_outcomes if v in negative_set]
        if pos_hits and neg_hits:
            conflicts.append(ConflictRecord(
                conflict_type="outcome",
                description=(
                    f"Contradictory outcomes: "
                    f"{pos_hits[0][0]} ({pos_hits[0][1]}) vs "
                    f"{neg_hits[0][0]} ({neg_hits[0][1]})"
                ),
                values=[f"{v}" for v, _, _ in pos_hits[:2]] + [f"{v}" for v, _, _ in neg_hits[:2]],
                sources=[s for _, s, _ in pos_hits[:2]] + [s for _, s, _ in neg_hits[:2]],
                article_ids=[i for _, _, i in pos_hits[:2]] + [i for _, _, i in neg_hits[:2]],
                severity="medium",
            ))

    return conflicts


# ── Full Story Cluster Builder ─────────────────────────────────────────────────

def build_story_clusters(
    articles: list[dict],
    threshold: float = SIMILARITY_THRESHOLD,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> list[ArticleCluster]:
    """
    Top-level function: takes raw article dicts and returns story clusters
    with common facts and conflicts extracted.

    Args:
        articles: list of dicts from DB (id, title, description, source_name,
                  topic, published_at, bias_score)
        threshold: similarity cutoff
        min_cluster_size: minimum articles to form a non-trivial cluster

    Returns:
        List of ArticleCluster objects, largest clusters first
    """
    if not articles:
        return []

    raw_clusters = cluster_articles(articles, threshold)

    result: list[ArticleCluster] = []
    for cluster_idx, indices in enumerate(raw_clusters):
        if len(indices) < min_cluster_size:
            continue

        members = [articles[i] for i in indices]

        # Compute pairwise similarities for the cluster report
        texts = [_article_text(a.get("title", ""), a.get("description", "")) for a in members]
        sim_scores: list[float] = []
        if len(texts) >= 2:
            try:
                vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=1)
                mat = vec.fit_transform(texts)
                sims = cosine_similarity(mat)
                # Collect upper-triangle scores
                for r in range(len(texts)):
                    for c in range(r + 1, len(texts)):
                        sim_scores.append(round(float(sims[r, c]), 3))
            except Exception:
                pass

        # Published_at range
        dates = [
            a.get("published_at") for a in members
            if a.get("published_at") is not None
        ]
        dates_str = [str(d) for d in sorted(dates)] if dates else []

        # Extract intelligence
        common = extract_common_facts(members)
        conflicts = detect_conflicts(members)

        cluster = ArticleCluster(
            cluster_id=f"cluster_{cluster_idx:04d}",
            article_ids=[a["id"] for a in members],
            article_titles=[a.get("title", "")[:100] for a in members],
            sources=sorted({a.get("source_name", "unknown") for a in members}),
            topic=_majority_topic(members),
            similarity_scores=sim_scores,
            common_facts=[asdict(f) for f in common],
            conflicts=[asdict(c) for c in conflicts],
            coverage_start=dates_str[0] if dates_str else None,
            coverage_end=dates_str[-1] if dates_str else None,
        )
        result.append(cluster)

    # Largest clusters first
    return sorted(result, key=lambda c: len(c.article_ids), reverse=True)


def _majority_topic(members: list[dict]) -> Optional[str]:
    topics = [a.get("topic") for a in members if a.get("topic")]
    if not topics:
        return None
    return Counter(topics).most_common(1)[0][0]


# ── DB Query Helper ────────────────────────────────────────────────────────────

def fetch_articles_for_clustering(db, hours: int = 24, topic: Optional[str] = None) -> list[dict]:
    """
    Fetch recent processed articles from DB for clustering.
    Returns plain dicts — no ORM dependency in the engine.
    """
    from sqlalchemy import select, text as sa_text
    from backend.models.article import Article
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    stmt = (
        select(Article)
        .where(Article.is_processed == True)
        .where(Article.fetched_at >= cutoff)
    )
    if topic:
        stmt = stmt.where(Article.topic == topic)
    stmt = stmt.order_by(Article.published_at.desc()).limit(500)

    articles = db.execute(stmt).scalars().all()
    return [
        {
            "id":           a.id,
            "title":        a.title or "",
            "description":  a.description or "",
            "source_name":  a.source_name,
            "topic":        a.topic,
            "bias_score":   a.bias_score,
            "sentiment_score": a.sentiment_score,
            "published_at": str(a.published_at) if a.published_at else None,
        }
        for a in articles
    ]
