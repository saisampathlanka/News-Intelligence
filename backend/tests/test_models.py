"""
Test database models and constraints.
Run: pytest backend/tests/test_models.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from backend.core.database import Base
from backend.models.article import Article
from backend.models.relations import RelatedArticle, TopicStats


@pytest.fixture
def db():
    """In-memory database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


# ── Schema Validation ─────────────────────────────────────────────────────────

def test_articles_table_exists(db):
    """Verify articles table is created."""
    inspector = inspect(db.bind)
    tables = inspector.get_table_names()
    assert 'articles' in tables


def test_related_articles_table_exists(db):
    """Verify related_articles table is created."""
    inspector = inspect(db.bind)
    tables = inspector.get_table_names()
    assert 'related_articles' in tables


def test_topic_stats_table_exists(db):
    """Verify topic_stats table is created."""
    inspector = inspect(db.bind)
    tables = inspector.get_table_names()
    assert 'topic_stats' in tables


# ── Article Model Tests ───────────────────────────────────────────────────────

def test_create_article(db):
    """Test basic article creation."""
    article = Article(
        content_hash="test_hash_123",
        url="https://test.com/article",
        title="Test Article",
        description="Test description",
        content="Test content",
        source_name="Test Source",
        source_type="rss",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    db.add(article)
    db.commit()
    
    assert article.id is not None
    assert article.fetched_at is not None  # auto-populated


def test_article_content_hash_unique(db):
    """Test content_hash uniqueness constraint."""
    article1 = Article(
        content_hash="duplicate_hash",
        url="https://test.com/1",
        title="Article 1",
        source_name="Source",
    )
    db.add(article1)
    db.commit()
    
    article2 = Article(
        content_hash="duplicate_hash",  # same hash
        url="https://test.com/2",
        title="Article 2",
        source_name="Source",
    )
    db.add(article2)
    
    with pytest.raises(IntegrityError):
        db.commit()


def test_article_url_unique(db):
    """Test URL uniqueness constraint."""
    article1 = Article(
        content_hash="hash1",
        url="https://test.com/same",
        title="Article 1",
        source_name="Source",
    )
    db.add(article1)
    db.commit()
    
    article2 = Article(
        content_hash="hash2",
        url="https://test.com/same",  # duplicate URL
        title="Article 2",
        source_name="Source",
    )
    db.add(article2)
    
    with pytest.raises(IntegrityError):
        db.commit()


def test_article_make_hash_deterministic():
    """Test that make_hash is deterministic."""
    h1 = Article.make_hash("https://example.com", "Title")
    h2 = Article.make_hash("https://example.com", "Title")
    assert h1 == h2


def test_article_make_hash_case_insensitive():
    """Test hash is case-insensitive."""
    h1 = Article.make_hash("https://Example.com", "Title")
    h2 = Article.make_hash("https://example.com", "TITLE")
    assert h1 == h2


# ── RelatedArticle Model Tests ────────────────────────────────────────────────

def test_create_related_article(db):
    """Test creating article relationships."""
    # Create two articles
    a1 = Article(content_hash="h1", url="http://t.co/1", title="A1", source_name="S")
    a2 = Article(content_hash="h2", url="http://t.co/2", title="A2", source_name="S")
    db.add_all([a1, a2])
    db.commit()
    
    # Create relationship
    relation = RelatedArticle(
        article_id=a1.id,
        related_id=a2.id,
        similarity_score=0.85,
        relation_type="topic",
    )
    db.add(relation)
    db.commit()
    
    assert relation.id is not None


def test_related_article_unique_pair(db):
    """Test that (article_id, related_id) is unique."""
    a1 = Article(content_hash="h1", url="http://t.co/1", title="A1", source_name="S")
    a2 = Article(content_hash="h2", url="http://t.co/2", title="A2", source_name="S")
    db.add_all([a1, a2])
    db.commit()
    
    r1 = RelatedArticle(article_id=a1.id, related_id=a2.id, similarity_score=0.8, relation_type="topic")
    db.add(r1)
    db.commit()
    
    r2 = RelatedArticle(article_id=a1.id, related_id=a2.id, similarity_score=0.9, relation_type="entity")
    db.add(r2)
    
    with pytest.raises(IntegrityError):
        db.commit()


# ── TopicStats Model Tests ────────────────────────────────────────────────────

def test_create_topic_stats(db):
    """Test creating topic statistics."""
    stats = TopicStats(
        topic="politics",
        article_count=100,
        avg_bias_score=-0.2,
        avg_sentiment=0.1,
    )
    db.add(stats)
    db.commit()
    
    assert stats.id is not None
    assert stats.last_updated is not None


def test_topic_stats_unique_topic(db):
    """Test topic uniqueness constraint."""
    s1 = TopicStats(topic="politics", article_count=10)
    db.add(s1)
    db.commit()
    
    s2 = TopicStats(topic="politics", article_count=20)
    db.add(s2)
    
    with pytest.raises(IntegrityError):
        db.commit()
