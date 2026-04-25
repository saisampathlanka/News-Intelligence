"""
Tests for processing module.
Run: pytest backend/tests/test_processing.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.article import Article
from backend.services.topic_classifier import classify_topic, classify_topic_multi
from backend.services.bias_detector import detect_bias, get_bias_confidence
from backend.services.nlp_processor import extract_keywords, analyze_sentiment, _top_mentions
from backend.services.processing import process_batch, _process_single


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def make_db_article(db, **kwargs) -> Article:
    """Helper to create article in DB."""
    defaults = {
        "content_hash": "test_hash_" + str(id(kwargs)),
        "url": "https://test.com/article",
        "title": "Test Article Title",
        "description": "Test description",
        "content": "Test content",
        "source_name": "Test Source",
        "source_type": "rss",
        "published_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    article = Article(**{**defaults, **kwargs})
    db.add(article)
    db.commit()
    db.refresh(article)
    return article


# ── Topic Classification ──────────────────────────────────────────────────────

def test_topic_politics():
    text = "The president announced new policy after meeting with congress members"
    assert classify_topic(text) == "politics"

def test_topic_business():
    text = "Stock market rallies as tech companies report strong revenue growth"
    assert classify_topic(text) == "business"

def test_topic_health():
    text = "New vaccine shows promise in clinical trials for treating disease"
    assert classify_topic(text) == "health"

def test_topic_technology():
    text = "AI startup launches innovative blockchain software platform"
    assert classify_topic(text) == "technology"

def test_topic_general_fallback():
    text = "Some random text with no clear category indicators"
    assert classify_topic(text) == "general"

def test_topic_multi():
    text = "Tech company stock rises as AI innovation drives market growth"
    topics = classify_topic_multi(text, threshold=2)
    assert "technology" in topics or "business" in topics


# ── Bias Detection ────────────────────────────────────────────────────────────

def test_bias_left():
    text = "Progressive activists demand social justice and universal healthcare to address inequality"
    score, label = detect_bias(text)
    assert score < 0
    assert label in ("left", "center-left")

def test_bias_right():
    text = "Conservative leaders advocate for fiscal responsibility and limited government with tax cuts"
    score, label = detect_bias(text)
    assert score > 0
    assert label in ("right", "center-right")

def test_bias_neutral():
    text = "According to data, experts say research indicates both sides have valid points"
    score, label = detect_bias(text)
    assert abs(score) < 0.3
    assert label == "center"

def test_bias_loaded_left():
    text = "Fascist attack on reproductive rights by corporate donors"
    score, label = detect_bias(text)
    assert score < -0.3  # Should be strongly left due to loaded language

def test_bias_loaded_right():
    text = "Radical left socialist mob pushes woke cancel culture agenda"
    score, label = detect_bias(text)
    assert score > 0.3  # Should be strongly right

def test_bias_confidence():
    assert get_bias_confidence(0.0) == 0.0
    assert get_bias_confidence(-0.8) > 0.8
    assert get_bias_confidence(0.5) >= 0.7


# ── NLP Processing ────────────────────────────────────────────────────────────

def test_top_mentions():
    items = ["A", "B", "A", "C", "A", "B"]
    result = _top_mentions(items, limit=2)
    assert result[0] == "A"  # most frequent
    assert result[1] == "B"

def test_sentiment_positive():
    text = "Great success with excellent progress and positive growth"
    score = analyze_sentiment(text)
    assert score > 0

def test_sentiment_negative():
    text = "Terrible disaster with devastating failure and tragic crisis"
    score = analyze_sentiment(text)
    assert score < 0

def test_sentiment_neutral():
    text = "The meeting occurred at the scheduled time in the building"
    score = analyze_sentiment(text)
    assert abs(score) < 0.3

def test_extract_keywords_empty():
    result = extract_keywords("")
    assert result == []


# ── Processing Pipeline ───────────────────────────────────────────────────────

def test_process_single_article(db):
    """Test that _process_single populates all fields."""
    article = make_db_article(
        db,
        title="President announces new healthcare policy",
        description="Government unveils universal healthcare plan",
        content="The new policy aims to provide coverage for all citizens",
        is_processed=False,
    )
    
    _process_single(article)
    
    assert article.topic in ("politics", "health")
    assert article.bias_score is not None
    assert article.bias_label is not None
    assert article.sentiment_score is not None
    assert article.entities_json is not None
    assert article.keywords_json is not None
    
    # Validate JSON structure
    entities = json.loads(article.entities_json)
    assert "people" in entities
    assert "organizations" in entities
    assert "places" in entities


def test_process_batch(db):
    """Test batch processing marks articles as processed."""
    # Create 3 unprocessed articles
    for i in range(3):
        make_db_article(
            db,
            content_hash=f"hash_{i}",
            url=f"https://test.com/{i}",
            title=f"Article {i} about politics and technology",
            is_processed=False,
        )
    
    result = process_batch(db, batch_size=10)
    
    assert result["processed"] == 3
    assert result["errors"] == 0
    
    # Check all are now processed
    processed_count = db.query(Article).filter_by(is_processed=True).count()
    assert processed_count == 3


def test_process_batch_respects_limit(db):
    """Test batch processing respects batch_size limit."""
    for i in range(5):
        make_db_article(
            db,
            content_hash=f"hash_{i}",
            url=f"https://test.com/{i}",
            title=f"Article {i}",
            is_processed=False,
        )
    
    result = process_batch(db, batch_size=3)
    assert result["processed"] == 3
    
    # 2 should remain unprocessed
    unprocessed = db.query(Article).filter_by(is_processed=False).count()
    assert unprocessed == 2


def test_process_batch_no_unprocessed(db):
    """Test batch processing with no unprocessed articles."""
    make_db_article(db, is_processed=True)
    
    result = process_batch(db, batch_size=10)
    assert result["processed"] == 0
