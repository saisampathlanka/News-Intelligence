"""
Processing pipeline: topic + bias + NLP for unprocessed articles.
Idempotent: only processes articles with is_processed=False.
"""
import json
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import select

from backend.models.article import Article
from backend.services.topic_classifier import classify_topic
from backend.services.bias_detector import detect_bias_full
from backend.services.nlp_processor import process_article_nlp

logger = logging.getLogger("news_intel.processing")


def process_batch(db: Session, batch_size: int = 100) -> dict:
    """
    Process up to batch_size unprocessed articles.
    Returns summary stats.
    """
    started = datetime.utcnow()
    
    # Fetch unprocessed articles
    stmt = (
        select(Article)
        .where(Article.is_processed == False)
        .order_by(Article.fetched_at.desc())
        .limit(batch_size)
    )
    articles = db.execute(stmt).scalars().all()
    
    if not articles:
        logger.info("No unprocessed articles")
        return {"processed": 0, "duration_sec": 0}
    
    processed_count = 0
    errors = 0
    
    for article in articles:
        try:
            _process_single(article)
            article.is_processed = True
            processed_count += 1
        except Exception as e:
            logger.error("Processing failed for article %d: %s", article.id, e)
            errors += 1
            # Don't mark as processed if it failed
    
    db.commit()
    
    duration = (datetime.utcnow() - started).total_seconds()
    result = {
        "processed": processed_count,
        "errors": errors,
        "duration_sec": round(duration, 2),
    }
    logger.info("Batch processing complete: %s", result)
    return result


def _process_single(article: Article):
    """Apply all processing steps to a single article."""
    
    # ── Topic classification ────────────────────────────────────
    combined_text = f"{article.title} {article.description or ''}"
    topic = classify_topic(combined_text)
    article.topic = topic
    
    # ── Bias detection ──────────────────────────────────────────
    full_text = f"{article.title} {article.description or ''} {article.content or ''}"
    bias_result = detect_bias_full(full_text, source_name=article.source_name)
    article.bias_score = bias_result.score
    article.bias_label = bias_result.label
    article.bias_confidence = bias_result.confidence
    article.bias_signals_json = json.dumps(bias_result.signals)
    
    # ── NLP: entities, keywords, sentiment ─────────────────────
    entities, keywords, sentiment = process_article_nlp(
        article.title,
        article.description or "",
        article.content or "",
    )
    article.entities_json = json.dumps(entities)
    article.keywords_json = json.dumps(keywords)
    article.sentiment_score = sentiment
    
    logger.debug(
        "Processed article %d: topic=%s, bias=%s (%.2f), sentiment=%.2f",
        article.id, topic, bias_label, bias_score, sentiment
    )


def reprocess_all(db: Session):
    """
    Mark all articles as unprocessed for reprocessing.
    Useful when processing logic changes.
    """
    from sqlalchemy import text
    db.execute(text("UPDATE articles SET is_processed = FALSE"))
    db.commit()
    logger.info("All articles marked for reprocessing")
