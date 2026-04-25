import logging
from datetime import datetime
from typing import List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from backend.models.article import Article
from backend.models.schemas import RawArticle
from backend.services.rss_fetcher import fetch_all_rss
from backend.services.api_fetchers import fetch_newsapi, fetch_guardian, fetch_gnews
from config.settings import settings

logger = logging.getLogger("news_intel.ingest")


def _to_db_article(raw: RawArticle) -> Article:
    return Article(
        content_hash=Article.make_hash(raw.url, raw.title),
        url=raw.url,
        title=raw.title,
        description=raw.description,
        content=raw.content,
        source_name=raw.source_name,
        source_type=raw.source_type,
        author=raw.author,
        language=raw.language,
        published_at=raw.published_at,
    )


def _save_articles(db: Session, raw_list: List[RawArticle]) -> Tuple[int, int]:
    """Persist articles. Returns (saved, skipped_dupes)."""
    saved, dupes = 0, 0
    for raw in raw_list:
        if not raw.is_valid(settings.MIN_ARTICLE_WORDS):
            continue
        article = _to_db_article(raw)
        try:
            db.add(article)
            db.flush()   # detect constraint violations early
            saved += 1
        except IntegrityError:
            db.rollback()
            dupes += 1
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("Commit failed: %s", e)
    return saved, dupes


def run_ingestion(db: Session) -> dict:
    """
    Full ingestion cycle:
      1. Fetch from all configured sources
      2. Deduplicate via content_hash
      3. Persist new articles
    Returns summary stats.
    """
    started = datetime.utcnow()
    all_raw: List[RawArticle] = []

    # ── RSS (always runs) ──────────────────────────────────────
    rss_articles = fetch_all_rss(
        settings.RSS_FEEDS,
        max_per_feed=settings.MAX_ARTICLES_PER_SOURCE,
    )
    all_raw.extend(rss_articles)
    logger.info("RSS total: %d", len(rss_articles))

    # ── NewsAPI (optional) ─────────────────────────────────────
    if settings.NEWSAPI_KEY:
        all_raw.extend(fetch_newsapi(settings.NEWSAPI_KEY, max_items=settings.MAX_ARTICLES_PER_SOURCE))

    # ── Guardian (optional) ────────────────────────────────────
    if settings.GUARDIAN_API_KEY:
        all_raw.extend(fetch_guardian(settings.GUARDIAN_API_KEY, max_items=settings.MAX_ARTICLES_PER_SOURCE))

    # ── GNews (optional) ──────────────────────────────────────
    if settings.GNEWS_API_KEY:
        all_raw.extend(fetch_gnews(settings.GNEWS_API_KEY))

    logger.info("Total fetched: %d articles", len(all_raw))

    # ── In-memory dedup before DB hit ─────────────────────────
    seen_hashes = set()
    unique_raw = []
    for raw in all_raw:
        h = Article.make_hash(raw.url, raw.title)
        if h not in seen_hashes:
            seen_hashes.add(h)
            unique_raw.append(raw)

    pre_dedup = len(all_raw) - len(unique_raw)
    saved, db_dupes = _save_articles(db, unique_raw)

    result = {
        "fetched": len(all_raw),
        "deduped_in_memory": pre_dedup,
        "deduped_in_db": db_dupes,
        "saved": saved,
        "duration_sec": round((datetime.utcnow() - started).total_seconds(), 2),
    }
    logger.info("Ingestion complete: %s", result)
    return result
