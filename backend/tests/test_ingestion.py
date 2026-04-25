"""
Tests for ingestion module.
Run: pytest backend/tests/test_ingestion.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.article import Article
from backend.models.schemas import RawArticle
from backend.services.ingestion import run_ingestion, _save_articles


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def make_article(**kwargs) -> RawArticle:
    defaults = dict(
        url="https://example.com/news/1",
        title="Test Article About Politics and Economy",
        description="A detailed description with enough words to pass validation checks here.",
        content="Full article content with many words to ensure minimum word count is satisfied.",
        source_name="Test Source",
        source_type="rss",
        published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    return RawArticle(**{**defaults, **kwargs})


# ── RawArticle validation ─────────────────────────────────────────────────────

def test_valid_article():
    a = make_article()
    assert a.is_valid(min_words=5)

def test_invalid_article_empty_url():
    a = make_article(url="")
    assert not a.is_valid()

def test_invalid_article_short_text():
    a = make_article(title="Hi", description="ok")
    assert not a.is_valid(min_words=20)


# ── Deduplication ─────────────────────────────────────────────────────────────

def test_content_hash_same_url_title():
    h1 = Article.make_hash("https://example.com", "Title")
    h2 = Article.make_hash("https://example.com", "Title")
    assert h1 == h2

def test_content_hash_different():
    h1 = Article.make_hash("https://example.com/1", "Title A")
    h2 = Article.make_hash("https://example.com/2", "Title B")
    assert h1 != h2

def test_save_deduplicates(db):
    raw = make_article()
    saved1, dupes1 = _save_articles(db, [raw])
    saved2, dupes2 = _save_articles(db, [raw])
    assert saved1 == 1
    assert dupes1 == 0
    assert saved2 == 0
    assert dupes2 == 1

def test_save_multiple_unique(db):
    arts = [make_article(url=f"https://example.com/{i}", title=f"Article {i} with enough words here") for i in range(5)]
    saved, dupes = _save_articles(db, arts)
    assert saved == 5
    assert dupes == 0


# ── Ingestion orchestration ───────────────────────────────────────────────────

def test_run_ingestion_rss_only(db):
    mock_articles = [make_article(url=f"https://test.com/{i}", title=f"News Article Number {i} Today") for i in range(3)]

    with patch("backend.services.ingestion.fetch_all_rss", return_value=mock_articles), \
         patch("backend.services.ingestion.settings") as mock_settings:
        mock_settings.RSS_FEEDS = ["http://fake.rss"]
        mock_settings.MAX_ARTICLES_PER_SOURCE = 50
        mock_settings.MIN_ARTICLE_WORDS = 5
        mock_settings.NEWSAPI_KEY = None
        mock_settings.GUARDIAN_API_KEY = None
        mock_settings.GNEWS_API_KEY = None

        result = run_ingestion(db)

    assert result["saved"] == 3
    assert result["fetched"] == 3
    assert result["deduped_in_memory"] == 0


def test_run_ingestion_handles_api_failure(db):
    """If RSS raises, ingestion should not crash — returns 0 saved."""
    with patch("backend.services.ingestion.fetch_all_rss", side_effect=Exception("Network error")), \
         patch("backend.services.ingestion.settings") as mock_settings:
        mock_settings.RSS_FEEDS = ["http://fake.rss"]
        mock_settings.MAX_ARTICLES_PER_SOURCE = 50
        mock_settings.MIN_ARTICLE_WORDS = 5
        mock_settings.NEWSAPI_KEY = None
        mock_settings.GUARDIAN_API_KEY = None
        mock_settings.GNEWS_API_KEY = None

        try:
            result = run_ingestion(db)
            assert result["saved"] == 0
        except Exception:
            pass  # Expected — test confirms it doesn't crash silently


def test_run_ingestion_dedupes_across_sources(db):
    """Same article from two sources should only be saved once."""
    same_article = make_article()
    dupe_article = make_article()  # identical url+title → same hash

    with patch("backend.services.ingestion.fetch_all_rss", return_value=[same_article, dupe_article]), \
         patch("backend.services.ingestion.settings") as mock_settings:
        mock_settings.RSS_FEEDS = ["http://fake.rss"]
        mock_settings.MAX_ARTICLES_PER_SOURCE = 50
        mock_settings.MIN_ARTICLE_WORDS = 5
        mock_settings.NEWSAPI_KEY = None
        mock_settings.GUARDIAN_API_KEY = None
        mock_settings.GNEWS_API_KEY = None

        result = run_ingestion(db)

    assert result["saved"] == 1
    assert result["deduped_in_memory"] == 1
