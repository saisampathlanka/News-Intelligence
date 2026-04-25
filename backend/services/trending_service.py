"""
Trending topics engine.
Scores topics and keywords by:
  - Article velocity (how many articles in recent window)
  - Entity mention frequency
  - Cross-source corroboration (same story across multiple outlets)
  - Sentiment spike (unusually strong positive/negative coverage)
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from backend.models.article import Article
from config.settings import settings

logger = logging.getLogger("news_intel.trending")


def _get_recent_articles(db: Session, hours: int) -> list[Article]:
    """Get processed articles from the last N hours."""
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    stmt = (
        select(Article)
        .where(Article.is_processed == True)
        .where(Article.fetched_at >= cutoff)
        .order_by(Article.fetched_at.desc())
    )
    return db.execute(stmt).scalars().all()


def get_trending_topics(
    db: Session,
    hours: Optional[int] = None,
    top_n: Optional[int] = None,
) -> list[dict]:
    """
    Compute trending topics from recent article activity.

    Scoring formula:
      score = (article_count * 2) + (source_diversity * 3) + (entity_mentions * 1.5) + sentiment_spike

    Returns top_n trending topics sorted by score descending.
    """
    hours = hours or settings.TRENDING_WINDOW_HOURS
    top_n = top_n or settings.TRENDING_TOP_N

    articles = _get_recent_articles(db, hours)
    if not articles:
        return []

    # ── Aggregate by topic ─────────────────────────────────────────────────
    topic_data: dict[str, dict] = defaultdict(lambda: {
        "article_count": 0,
        "sources": set(),
        "keywords": Counter(),
        "entities": Counter(),
        "sentiments": [],
        "bias_scores": [],
        "sample_headlines": [],
    })

    for article in articles:
        topic = article.topic or "general"
        td = topic_data[topic]

        td["article_count"] += 1
        td["sources"].add(article.source_name)

        if len(td["sample_headlines"]) < 3:
            td["sample_headlines"].append(article.title)

        if article.sentiment_score is not None:
            td["sentiments"].append(article.sentiment_score)

        if article.bias_score is not None:
            td["bias_scores"].append(article.bias_score)

        if article.keywords_json:
            try:
                kws = json.loads(article.keywords_json)
                td["keywords"].update(kws[:5])  # top 5 per article
            except Exception:
                pass

        if article.entities_json:
            try:
                ents = json.loads(article.entities_json)
                for entity_list in ents.values():
                    td["entities"].update(entity_list[:3])
            except Exception:
                pass

    # ── Score each topic ────────────────────────────────────────────────────
    trending = []

    # Calculate baseline for spike detection
    avg_articles = sum(d["article_count"] for d in topic_data.values()) / max(len(topic_data), 1)

    for topic, data in topic_data.items():
        count = data["article_count"]
        source_div = len(data["sources"])  # number of unique sources covering it
        entity_mentions = len(data["entities"])

        # Sentiment spike: unusually strong sentiment (absolute)
        sentiments = data["sentiments"]
        avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0
        sentiment_spike = abs(avg_sentiment) * 2 if abs(avg_sentiment) > 0.4 else 0

        # Velocity bonus: trending faster than average
        velocity_bonus = max(0, (count - avg_articles) / max(avg_articles, 1)) * 5

        score = (
            count * 2
            + source_div * 3
            + entity_mentions * 1.5
            + sentiment_spike
            + velocity_bonus
        )

        # Bias variance across sources (higher = more contested topic)
        bias_scores = data["bias_scores"]
        if len(bias_scores) >= 2:
            bias_variance = max(bias_scores) - min(bias_scores)
        else:
            bias_variance = 0.0

        trending.append({
            "topic": topic,
            "score": round(score, 1),
            "article_count": count,
            "source_count": source_div,
            "sources": sorted(data["sources"]),
            "top_keywords": [kw for kw, _ in data["keywords"].most_common(8)],
            "top_entities": [ent for ent, _ in data["entities"].most_common(5)],
            "avg_sentiment": round(avg_sentiment, 2),
            "sentiment_label": _sentiment_label(avg_sentiment),
            "bias_variance": round(bias_variance, 2),
            "is_contested": bias_variance > 0.5,
            "sample_headlines": data["sample_headlines"],
            "trend_velocity": round(velocity_bonus, 1),
            "window_hours": hours,
        })

    # Sort by score descending
    trending.sort(key=lambda x: x["score"], reverse=True)
    return trending[:top_n]


def get_trending_entities(db: Session, hours: int = 24, top_n: int = 20) -> list[dict]:
    """
    Get the most mentioned people, organizations, and places recently.
    """
    articles = _get_recent_articles(db, hours)
    people: Counter = Counter()
    orgs: Counter = Counter()
    places: Counter = Counter()

    for article in articles:
        if not article.entities_json:
            continue
        try:
            ents = json.loads(article.entities_json)
            people.update(ents.get("people", []))
            orgs.update(ents.get("organizations", []))
            places.update(ents.get("places", []))
        except Exception:
            pass

    return {
        "people":        [{"name": n, "count": c} for n, c in people.most_common(top_n)],
        "organizations": [{"name": n, "count": c} for n, c in orgs.most_common(top_n)],
        "places":        [{"name": n, "count": c} for n, c in places.most_common(top_n)],
        "window_hours":  hours,
    }


def get_cross_source_stories(db: Session, hours: int = 12, min_sources: int = 3) -> list[dict]:
    """
    Find stories covered by multiple sources — higher credibility signal.
    Groups articles by keyword overlap and counts source diversity.
    """
    articles = _get_recent_articles(db, hours)
    keyword_groups: dict[str, dict] = defaultdict(lambda: {"articles": [], "sources": set()})

    for article in articles:
        if not article.keywords_json:
            continue
        try:
            kws = json.loads(article.keywords_json)[:3]  # top 3 keywords as group key
            key = "|".join(sorted(kws[:2]))  # normalize pair
            if key:
                keyword_groups[key]["articles"].append({
                    "id": article.id,
                    "title": article.title,
                    "source": article.source_name,
                    "bias_label": article.bias_label,
                })
                keyword_groups[key]["sources"].add(article.source_name)
        except Exception:
            pass

    # Filter to stories with min_sources
    stories = []
    for key, data in keyword_groups.items():
        if len(data["sources"]) >= min_sources:
            stories.append({
                "keywords": key.split("|"),
                "source_count": len(data["sources"]),
                "sources": sorted(data["sources"]),
                "article_count": len(data["articles"]),
                "articles": data["articles"][:5],
            })

    stories.sort(key=lambda x: x["source_count"], reverse=True)
    return stories[:10]


def _sentiment_label(score: float) -> str:
    if score > 0.3:
        return "positive"
    elif score < -0.3:
        return "negative"
    else:
        return "neutral"
