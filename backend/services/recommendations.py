"""
Recommendation Engine — upgraded design.

Two complementary systems:

1. Article Recommendations  (find_similar_articles)
   Four weighted signals, each contributing independently:
     a) Topic match          — same category (base 0.40)
     b) Entity Jaccard       — shared people/orgs/places  (weight 0.30)
     c) Keyword Jaccard      — shared noun phrases        (weight 0.20)
     d) Perspective bonus    — bias divergence > 0.4      (bonus 0.10)

   Scoring formula per candidate:
     score = Σ(signal_value × signal_weight) / Σ(weights)

2. Topic Graph  (build_topic_graph / get_related_topics)
   Nodes  = topic categories
   Edges  = normalized co-occurrence frequency across articles

   An article that spans politics + technology adds 1 to the
   politics↔technology edge. Edge weight = co_count / total_articles.
   This surfaces "if you're reading politics, also check business"
   style relationships.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from backend.models.article import Article
from backend.models.relations import RelatedArticle

logger = logging.getLogger("news_intel.recommendations")

# ── Signal weights ────────────────────────────────────────────────────────────
W_TOPIC    = 0.40
W_ENTITY   = 0.30
W_KEYWORD  = 0.20
W_PERSP    = 0.10   # perspective diversity bonus

BIAS_DIVERSITY_THRESHOLD = 0.35   # min bias difference to earn perspective bonus
MIN_ENTITY_JACCARD       = 0.05   # skip entity signal if overlap below this
MIN_KEYWORD_JACCARD      = 0.05

# Topic graph
GRAPH_MIN_EDGE_WEIGHT = 0.02      # edges below this are pruned


# ── Topic Graph ───────────────────────────────────────────────────────────────

@dataclass
class TopicEdge:
    topic_a: str
    topic_b: str
    co_count: int
    weight: float   # co_count / total_articles

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TopicGraph:
    nodes: list[str]                   # all topics present
    edges: list[TopicEdge]             # co-occurrence edges
    total_articles: int

    def neighbors(self, topic: str, top_n: int = 5) -> list[tuple[str, float]]:
        """Return top_n related topics for a given topic, sorted by weight desc."""
        related = []
        for edge in self.edges:
            if edge.topic_a == topic:
                related.append((edge.topic_b, edge.weight))
            elif edge.topic_b == topic:
                related.append((edge.topic_a, edge.weight))
        return sorted(related, key=lambda x: -x[1])[:top_n]

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "edges": [e.to_dict() for e in self.edges],
            "total_articles": self.total_articles,
        }


def build_topic_graph(db: Session) -> TopicGraph:
    """
    Build a topic co-occurrence graph from all processed articles.

    An article's topic contributes a self-edge (topic frequency).
    If an article mentions two topics (via its keywords), we record a
    co-occurrence edge between them.

    Since each article has one topic, we approximate cross-topic links
    by checking keyword overlap between topic groups.
    """
    # Fetch topic + keywords for all processed articles
    rows = db.execute(
        select(Article.topic, Article.keywords_json)
        .where(Article.is_processed == True)
        .where(Article.topic != None)
    ).all()

    total = len(rows)
    if total == 0:
        return TopicGraph(nodes=[], edges=[], total_articles=0)

    # Count per-topic frequencies
    topic_counts: dict[str, int] = defaultdict(int)
    # Co-occurrence: pair of topics -> count
    co_matrix: dict[tuple[str, str], int] = defaultdict(int)

    # Build topic → keyword index for co-occurrence detection
    topic_keywords: dict[str, set] = defaultdict(set)
    for row in rows:
        t = row.topic
        topic_counts[t] += 1
        if row.keywords_json:
            try:
                kws = set(json.loads(row.keywords_json))
                topic_keywords[t].update(kws)
            except Exception:
                pass

    # Record co-occurrence between topics that share keywords
    topics = list(topic_keywords.keys())
    for i in range(len(topics)):
        for j in range(i + 1, len(topics)):
            ta, tb = topics[i], topics[j]
            shared = topic_keywords[ta] & topic_keywords[tb]
            if shared:
                co_count = min(topic_counts[ta], topic_counts[tb], len(shared))
                if co_count > 0:
                    key = (min(ta, tb), max(ta, tb))
                    co_matrix[key] += co_count

    # Build edges, normalize by total articles
    edges = []
    for (ta, tb), count in co_matrix.items():
        weight = round(count / max(total, 1), 4)
        if weight >= GRAPH_MIN_EDGE_WEIGHT:
            edges.append(TopicEdge(
                topic_a=ta, topic_b=tb,
                co_count=count, weight=weight,
            ))

    # Sort edges by weight descending
    edges.sort(key=lambda e: -e.weight)

    return TopicGraph(
        nodes=sorted(topic_counts.keys()),
        edges=edges,
        total_articles=total,
    )


def get_related_topics(db: Session, topic: str, top_n: int = 5) -> list[dict]:
    """
    Return top_n topics related to the given topic via the co-occurrence graph.
    """
    graph = build_topic_graph(db)
    neighbors = graph.neighbors(topic, top_n=top_n)

    if not neighbors:
        # Fallback: return topics with highest article count
        rows = db.execute(
            select(Article.topic, func.count(Article.id).label("cnt"))
            .where(Article.is_processed == True)
            .where(Article.topic != None)
            .where(Article.topic != topic)
            .group_by(Article.topic)
            .order_by(func.count(Article.id).desc())
            .limit(top_n)
        ).all()
        return [{"topic": r.topic, "weight": 0.0, "relation": "by_volume"} for r in rows]

    return [
        {"topic": t, "weight": round(w, 4), "relation": "co_occurrence"}
        for t, w in neighbors
    ]


# ── Article Similarity ────────────────────────────────────────────────────────

def _jaccard_similarity(set1: set, set2: set) -> float:
    """Jaccard index. Returns 0.0 if either set is empty."""
    if not set1 or not set2:
        return 0.0
    inter = len(set1 & set2)
    union = len(set1 | set2)
    return inter / union if union > 0 else 0.0


def _parse_entities(entities_json: Optional[str]) -> set:
    """Flatten entity dict to a set of lowercase strings."""
    if not entities_json:
        return set()
    try:
        d = json.loads(entities_json)
        flat = set()
        for entity_list in d.values():
            flat.update(e.lower() for e in entity_list if e)
        return flat
    except Exception:
        return set()


def _parse_keywords(keywords_json: Optional[str]) -> set:
    if not keywords_json:
        return set()
    try:
        return set(json.loads(keywords_json))
    except Exception:
        return set()


def _score_candidate(
    source: Article,
    candidate: Article,
    source_entities: set,
    source_keywords: set,
) -> tuple[float, str]:
    """
    Score a candidate article against the source.

    Returns (composite_score, primary_signal_name).
    composite_score is 0.0 if below minimum threshold.
    """
    weighted_scores: list[tuple[str, float, float]] = []  # (name, value, weight)

    # Signal A: topic match
    if source.topic and candidate.topic == source.topic:
        weighted_scores.append(("topic", 1.0, W_TOPIC))

    # Signal B: entity Jaccard
    cand_entities = _parse_entities(candidate.entities_json)
    ej = _jaccard_similarity(source_entities, cand_entities)
    if ej >= MIN_ENTITY_JACCARD:
        weighted_scores.append(("entity", ej, W_ENTITY))

    # Signal C: keyword Jaccard
    cand_keywords = _parse_keywords(candidate.keywords_json)
    kj = _jaccard_similarity(source_keywords, cand_keywords)
    if kj >= MIN_KEYWORD_JACCARD:
        weighted_scores.append(("keyword", kj, W_KEYWORD))

    # Signal D: perspective diversity bonus
    if (source.bias_score is not None and candidate.bias_score is not None):
        diff = abs(source.bias_score - candidate.bias_score)
        if diff >= BIAS_DIVERSITY_THRESHOLD:
            weighted_scores.append(("perspective", diff / 2.0, W_PERSP))

    if not weighted_scores:
        return 0.0, "none"

    # Weighted average (normalize by sum of weights used)
    total_weight = sum(w for _, _, w in weighted_scores)
    composite = sum(v * w for _, v, w in weighted_scores) / total_weight

    # Primary signal = the one contributing most to the score
    primary = max(weighted_scores, key=lambda x: x[1] * x[2])[0]

    return round(composite, 4), primary


def find_similar_articles(
    db: Session,
    article_id: int,
    limit: int = 5,
    min_score: float = 0.15,
) -> list[tuple[Article, float, str]]:
    """
    Find similar articles using 4 weighted signals.

    Returns: [(article, score, relation_type), ...] sorted by score desc.

    Checks pre-computed relationships first (fast path).
    Falls back to on-demand scoring if none exist yet.
    """
    source = db.query(Article).filter(Article.id == article_id).first()
    if not source or not source.is_processed:
        return []

    # ── Fast path: pre-computed relationships ────────────────────────────────
    existing = (
        db.query(RelatedArticle, Article)
        .join(Article, RelatedArticle.related_id == Article.id)
        .filter(RelatedArticle.article_id == article_id)
        .order_by(RelatedArticle.similarity_score.desc())
        .limit(limit)
        .all()
    )
    if existing:
        return [(art, rel.similarity_score, rel.relation_type) for rel, art in existing]

    # ── On-demand scoring ─────────────────────────────────────────────────────
    source_entities = _parse_entities(source.entities_json)
    source_keywords = _parse_keywords(source.keywords_json)

    # Candidate pool: same topic first (up to 200), then recent articles
    candidate_query = (
        select(Article)
        .where(Article.id != article_id)
        .where(Article.is_processed == True)
        .order_by(Article.published_at.desc())
        .limit(200)
    )
    if source.topic:
        candidate_query = candidate_query.where(Article.topic == source.topic)

    candidates_raw = db.execute(candidate_query).scalars().all()

    scored = []
    for candidate in candidates_raw:
        score, primary = _score_candidate(source, candidate, source_entities, source_keywords)
        if score >= min_score:
            scored.append((candidate, score, primary))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def precompute_relationships(db: Session, batch_size: int = 50) -> int:
    """
    Pre-compute article similarity for recently ingested articles.
    Stores results in related_articles table for O(1) lookup at request time.
    Called by the scheduler every hour.

    Returns count of articles processed.
    """
    # Only process articles that don't yet have relationships stored
    subquery = select(RelatedArticle.article_id).distinct()
    query = (
        select(Article)
        .where(Article.is_processed == True)
        .where(Article.id.not_in(subquery))
        .order_by(Article.fetched_at.desc())
        .limit(batch_size)
    )
    articles = db.execute(query).scalars().all()

    for article in articles:
        similar = find_similar_articles(db, article.id, limit=5, min_score=0.15)
        for related_article, score, rel_type in similar:
            try:
                db.add(RelatedArticle(
                    article_id=article.id,
                    related_id=related_article.id,
                    similarity_score=round(score, 4),
                    relation_type=rel_type,
                ))
                db.flush()
            except Exception:
                db.rollback()
                logger.debug("Skipping duplicate relationship %d→%d", article.id, related_article.id)

    db.commit()
    logger.info("Precomputed relationships for %d articles", len(articles))
    return len(articles)
