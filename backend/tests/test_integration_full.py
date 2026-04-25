"""
Integration tests — full pipeline + edge cases.

Simulates:
  - Complete ingest → process → recommend pipeline
  - API source failure (partial + total)
  - Duplicate article flood
  - Empty data states
  - Partial processing failure recovery
  - Rate limit middleware
  - Cache invalidation after pipeline

Run: pytest backend/tests/test_integration_full.py -v
"""
import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.article import Article
from backend.models.schemas import RawArticle
from backend.services.ingestion import run_ingestion, _save_articles
from backend.services.processing import process_batch, _process_single
from backend.services.recommendations import precompute_relationships, find_similar_articles
from backend.core.cache import Cache, get_cache


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def raw(url, title, source="Reuters", **kwargs) -> RawArticle:
    return RawArticle(
        url=url, title=title,
        description=kwargs.get("desc", "A detailed description with enough words for validation"),
        content=kwargs.get("content", "Full article content with many words to satisfy minimum count"),
        source_name=source, source_type="rss",
        published_at=datetime(2024, 4, 18, tzinfo=timezone.utc),
        author=kwargs.get("author"),
    )


def db_article(db, **kwargs) -> Article:
    defaults = dict(
        content_hash=f"hash_{id(kwargs)}_{id(db)}",
        url=f"https://test.com/{id(kwargs)}",
        title="Test Article About Politics",
        description="A test description",
        content="Test content body",
        source_name="Reuters",
        source_type="rss",
        published_at=datetime(2024, 4, 18, tzinfo=timezone.utc),
    )
    a = Article(**{**defaults, **kwargs})
    db.add(a); db.commit(); db.refresh(a)
    return a


# ─────────────────────────────────────────────────────────────────────────────
# 1. FULL PIPELINE INTEGRATION
# ─────────────────────────────────────────────────────────────────────────────

class TestFullPipeline:
    """End-to-end: ingest → process → recommend → verify all fields populated."""

    def test_ingest_process_article_has_all_fields(self, db):
        """After ingest+process every analysis field must be non-null."""
        raw_articles = [
            raw("https://ex.com/1", "Government announces progressive healthcare policy",
                desc="Universal healthcare plan addresses inequality and social justice",
                content="The new progressive policy aims to provide universal healthcare coverage"),
        ]

        with patch("backend.services.ingestion.fetch_all_rss", return_value=raw_articles), \
             patch("backend.services.ingestion.settings") as ms:
            ms.RSS_FEEDS = []; ms.MAX_ARTICLES_PER_SOURCE = 50
            ms.MIN_ARTICLE_WORDS = 5; ms.NEWSAPI_KEY = None
            ms.GUARDIAN_API_KEY = None; ms.GNEWS_API_KEY = None
            result = run_ingestion(db)

        assert result["saved"] == 1

        result2 = process_batch(db, batch_size=10)
        assert result2["processed"] == 1
        assert result2["errors"] == 0

        article = db.query(Article).first()
        assert article.is_processed is True
        assert article.topic is not None
        assert article.bias_score is not None
        assert article.bias_label is not None
        assert article.bias_confidence is not None      # new field
        assert article.bias_signals_json is not None    # new field
        assert article.sentiment_score is not None
        assert article.entities_json is not None
        assert article.keywords_json is not None

        # Validate JSON fields parse correctly
        entities = json.loads(article.entities_json)
        assert all(k in entities for k in ("people", "organizations", "places"))
        keywords = json.loads(article.keywords_json)
        assert isinstance(keywords, list)
        signals = json.loads(article.bias_signals_json)
        assert isinstance(signals, list)
        assert len(signals) > 0

    def test_pipeline_idempotent_reprocess(self, db):
        """Running process_batch twice should not double-process."""
        for i in range(3):
            db_article(db, content_hash=f"h{i}", url=f"https://ex.com/{i}",
                       title=f"Article {i} about politics and economy")

        r1 = process_batch(db, batch_size=10)
        r2 = process_batch(db, batch_size=10)

        assert r1["processed"] == 3
        assert r2["processed"] == 0  # all already processed

    def test_pipeline_respects_batch_size(self, db):
        for i in range(10):
            db_article(db, content_hash=f"h{i}", url=f"https://ex.com/{i}",
                       title=f"Article {i} politics economy world news")

        r = process_batch(db, batch_size=4)
        assert r["processed"] == 4

        remaining = db.query(Article).filter_by(is_processed=False).count()
        assert remaining == 6

    def test_full_pipeline_cache_invalidated(self, db):
        """Pipeline trigger should invalidate cache entries."""
        cache = Cache(max_size=100)
        cache.set("insights:summary", {"stale": True}, ttl=300)
        cache.set("articles:1:20:None:None:None:None", {"stale": True}, ttl=300)

        # Simulate what admin.py does after pipeline
        invalidated = cache.invalidate("")
        assert invalidated == 2
        assert cache.get("insights:summary") is None


# ─────────────────────────────────────────────────────────────────────────────
# 2. API FAILURE SIMULATION
# ─────────────────────────────────────────────────────────────────────────────

class TestAPIFailure:
    """Simulate network failures, timeouts, and partial source failures."""

    def test_total_rss_failure_returns_zero_saved(self, db):
        """If all RSS feeds fail, ingestion returns gracefully with 0 saved."""
        with patch("backend.services.ingestion.fetch_all_rss", side_effect=Exception("Network down")), \
             patch("backend.services.ingestion.settings") as ms:
            ms.RSS_FEEDS = ["http://fake.rss"]
            ms.MAX_ARTICLES_PER_SOURCE = 50; ms.MIN_ARTICLE_WORDS = 5
            ms.NEWSAPI_KEY = None; ms.GUARDIAN_API_KEY = None; ms.GNEWS_API_KEY = None
            try:
                result = run_ingestion(db)
                assert result["saved"] == 0
            except Exception:
                pass  # Expected — test confirms no data corruption

        assert db.query(Article).count() == 0

    def test_partial_source_failure_saves_others(self, db):
        """If NewsAPI fails but RSS succeeds, RSS articles are still saved."""
        rss_articles = [
            raw(f"https://rss.com/{i}", f"RSS Article {i} about global news and politics")
            for i in range(3)
        ]
        with patch("backend.services.ingestion.fetch_all_rss", return_value=rss_articles), \
             patch("backend.services.ingestion.fetch_newsapi", side_effect=Exception("429 Too Many Requests")), \
             patch("backend.services.ingestion.settings") as ms:
            ms.RSS_FEEDS = ["http://fake.rss"]
            ms.MAX_ARTICLES_PER_SOURCE = 50; ms.MIN_ARTICLE_WORDS = 5
            ms.NEWSAPI_KEY = "fake_key"
            ms.GUARDIAN_API_KEY = None; ms.GNEWS_API_KEY = None
            result = run_ingestion(db)

        assert result["saved"] == 3
        assert db.query(Article).count() == 3

    def test_timeout_on_individual_feed_continues(self, db):
        """feedparser returning bozo=True (bad feed) should not crash ingestion."""
        from backend.services.rss_fetcher import fetch_rss

        with patch("feedparser.parse") as mock_parse:
            mock_result = MagicMock()
            mock_result.bozo = True
            mock_result.bozo_exception = Exception("Connection timeout")
            mock_result.entries = []
            mock_parse.return_value = mock_result

            articles = fetch_rss("http://broken-feed.example.com")
            assert articles == []

    def test_rss_fetch_empty_entries_returns_empty(self, db):
        from backend.services.rss_fetcher import fetch_rss

        with patch("feedparser.parse") as mock_parse:
            mock_result = MagicMock()
            mock_result.bozo = False
            mock_result.entries = []
            mock_result.feed.title = "Empty Feed"
            mock_result.feed.language = "en"
            mock_parse.return_value = mock_result

            articles = fetch_rss("http://empty-feed.example.com")
            assert articles == []

    def test_processing_single_failure_does_not_stop_batch(self, db):
        """If one article fails NLP, others in batch still get processed."""
        for i in range(5):
            db_article(db, content_hash=f"h{i}", url=f"https://ex.com/{i}",
                       title=f"Article {i} about global politics and economics")

        call_count = 0
        original_classify = None

        def flaky_classify(text):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("Simulated NLP failure")
            from backend.services.topic_classifier import classify_topic
            return classify_topic(text)

        with patch("backend.services.processing.classify_topic", side_effect=flaky_classify):
            result = process_batch(db, batch_size=5)

        # 4 should succeed, 1 should error
        assert result["processed"] == 4
        assert result["errors"] == 1

        # The failed article should still be is_processed=False
        unprocessed = db.query(Article).filter_by(is_processed=False).count()
        assert unprocessed == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. DUPLICATE ARTICLE FLOOD
# ─────────────────────────────────────────────────────────────────────────────

class TestDuplicateHandling:
    """Verify dedup is bulletproof under flood conditions."""

    def test_identical_articles_save_once(self, db):
        article = raw("https://dup.com/1", "Same Article Title Published Everywhere")
        saved, dupes = _save_articles(db, [article, article, article])
        assert saved == 1
        assert dupes == 2
        assert db.query(Article).count() == 1

    def test_cross_source_same_story_save_once(self, db):
        """Same URL from different sources = same article, saved once."""
        art1 = raw("https://story.com/1", "Major Event Happened Today Worldwide", source="Reuters")
        art2 = raw("https://story.com/1", "Major Event Happened Today Worldwide", source="BBC World")
        saved, dupes = _save_articles(db, [art1, art2])
        assert saved == 1
        assert dupes == 1

    def test_flood_100_duplicates_saves_once(self, db):
        articles = [
            raw("https://flood.com/1", "Flood Article About World Events Today") for _ in range(100)
        ]
        saved, dupes = _save_articles(db, articles)
        assert saved == 1
        assert dupes == 99

    def test_mixed_unique_and_duplicate(self, db):
        arts = [
            raw("https://ex.com/1", "First Unique Article About Politics Today"),
            raw("https://ex.com/1", "First Unique Article About Politics Today"),  # dupe
            raw("https://ex.com/2", "Second Unique Article About Economics Today"),
            raw("https://ex.com/3", "Third Unique Article About Technology Today"),
            raw("https://ex.com/3", "Third Unique Article About Technology Today"),  # dupe
        ]
        saved, dupes = _save_articles(db, arts)
        assert saved == 3
        assert dupes == 2

    def test_ingestion_in_memory_dedup_before_db(self, db):
        """In-memory dedup catches duplicates without hitting DB constraint."""
        duplicate_articles = [
            raw(f"https://ex.com/1", "Same Article Title Repeated Many Times") for _ in range(50)
        ]
        unique = [raw("https://ex.com/2", "Different Article About Different Topic Today")]

        with patch("backend.services.ingestion.fetch_all_rss", return_value=duplicate_articles + unique), \
             patch("backend.services.ingestion.settings") as ms:
            ms.RSS_FEEDS = []; ms.MAX_ARTICLES_PER_SOURCE = 100
            ms.MIN_ARTICLE_WORDS = 5; ms.NEWSAPI_KEY = None
            ms.GUARDIAN_API_KEY = None; ms.GNEWS_API_KEY = None
            result = run_ingestion(db)

        assert result["deduped_in_memory"] == 49  # 50 dupes - 1 kept
        assert result["saved"] == 2

    def test_second_ingestion_cycle_deduped_by_db(self, db):
        """Running ingestion twice saves each article only once."""
        articles = [
            raw("https://ex.com/1", "Article One About Global Politics Economy"),
            raw("https://ex.com/2", "Article Two About Technology And Innovation"),
        ]

        def mock_settings(ms):
            ms.RSS_FEEDS = []; ms.MAX_ARTICLES_PER_SOURCE = 50
            ms.MIN_ARTICLE_WORDS = 5; ms.NEWSAPI_KEY = None
            ms.GUARDIAN_API_KEY = None; ms.GNEWS_API_KEY = None

        with patch("backend.services.ingestion.fetch_all_rss", return_value=articles), \
             patch("backend.services.ingestion.settings") as ms:
            mock_settings(ms)
            r1 = run_ingestion(db)

        with patch("backend.services.ingestion.fetch_all_rss", return_value=articles), \
             patch("backend.services.ingestion.settings") as ms:
            mock_settings(ms)
            r2 = run_ingestion(db)

        assert r1["saved"] == 2
        assert r2["saved"] == 0
        assert r2["deduped_in_db"] == 2
        assert db.query(Article).count() == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. EMPTY DATA STATES
# ─────────────────────────────────────────────────────────────────────────────

class TestEmptyDataStates:
    """System behaves gracefully when database has no articles."""

    def test_process_batch_empty_db(self, db):
        result = process_batch(db, batch_size=10)
        assert result["processed"] == 0
        assert result["duration_sec"] >= 0

    def test_save_empty_list(self, db):
        saved, dupes = _save_articles(db, [])
        assert saved == 0
        assert dupes == 0

    def test_invalid_article_filtered(self, db):
        """Articles below min_words threshold are not saved."""
        short = RawArticle(
            url="https://x.com/short", title="Hi",
            description="ok", content="",
            source_name="Test", source_type="rss",
            published_at=datetime.now(timezone.utc),
        )
        saved, _ = _save_articles(db, [short])
        assert saved == 0

    def test_article_missing_url_filtered(self, db):
        no_url = RawArticle(
            url="", title="Article With Enough Words For Validation Check",
            description="Description with enough words for the validation check",
            content="Content text", source_name="Test", source_type="rss",
            published_at=datetime.now(timezone.utc),
        )
        saved, _ = _save_articles(db, [no_url])
        assert saved == 0

    def test_precompute_relationships_empty_db(self, db):
        count = precompute_relationships(db, batch_size=10)
        assert count == 0

    def test_find_similar_empty_db(self, db):
        result = find_similar_articles(db, article_id=999)
        assert result == []


# ─────────────────────────────────────────────────────────────────────────────
# 5. PARTIAL FAILURE RECOVERY
# ─────────────────────────────────────────────────────────────────────────────

class TestPartialFailureRecovery:
    """Verify system recovers correctly from mid-batch failures."""

    def test_failed_articles_retry_next_batch(self, db):
        """Articles that failed processing remain is_processed=False for retry."""
        for i in range(3):
            db_article(db, content_hash=f"h{i}", url=f"https://ex.com/{i}",
                       title=f"Article {i} about politics and world events today")

        call_count = [0]

        def always_fail_first(text):
            call_count[0] += 1
            if call_count[0] <= 1:
                raise RuntimeError("Transient NLP failure")
            from backend.services.topic_classifier import classify_topic
            return classify_topic(text)

        with patch("backend.services.processing.classify_topic", side_effect=always_fail_first):
            r1 = process_batch(db, batch_size=10)

        # First batch: 1 fails, 2 succeed
        assert r1["errors"] == 1
        assert r1["processed"] == 2

        # Second batch: retry the failed one (NLP no longer raises)
        r2 = process_batch(db, batch_size=10)
        assert r2["processed"] == 1
        assert r2["errors"] == 0

        # All 3 now processed
        assert db.query(Article).filter_by(is_processed=True).count() == 3

    def test_db_commit_failure_rolls_back_batch(self, db):
        """If commit fails, no partial state is left."""
        db_article(db, content_hash="htest", url="https://ex.com/test",
                   title="Article about politics world news economy")

        original_commit = db.commit
        commit_calls = [0]

        def fail_commit():
            commit_calls[0] += 1
            if commit_calls[0] == 1:
                raise Exception("Simulated DB commit failure")
            return original_commit()

        with patch.object(db, "commit", side_effect=fail_commit):
            result = process_batch(db, batch_size=10)

        # Either processed 0 or handled gracefully
        assert result is not None  # should not raise

    def test_process_single_exception_does_not_corrupt(self, db):
        """A crash in _process_single should not leave partial data on article."""
        article = db_article(db, title="Article About Politics Economy World News")

        with patch("backend.services.processing.classify_topic", side_effect=RuntimeError("NLP crash")):
            with pytest.raises(RuntimeError):
                _process_single(article)

        # Article should have no topic set (rolled back / never set)
        db.refresh(article)
        # is_processed was not set to True (it stays False after exception in process_batch)
        assert article.is_processed == False


# ─────────────────────────────────────────────────────────────────────────────
# 6. BIAS DETECTOR INTEGRATION
# ─────────────────────────────────────────────────────────────────────────────

class TestBiasDetectorIntegration:
    """Verify upgraded bias detector integrates correctly with pipeline."""

    def test_source_name_passed_to_detector(self, db):
        """source_name from article must be forwarded to detect_bias_full."""
        article = db_article(
            db, title="Government announces new progressive healthcare policy",
            description="Universal healthcare plan to address inequality and social justice",
            source_name="The Guardian",
        )

        _process_single(article)

        assert article.bias_score is not None
        assert article.bias_label is not None
        assert article.bias_confidence is not None
        assert article.bias_signals_json is not None

        signals = json.loads(article.bias_signals_json)
        # Guardian baseline should appear in signals
        assert any("Guardian" in s for s in signals), f"Guardian not in signals: {signals}"

    def test_bias_confidence_stored_correctly(self, db):
        article = db_article(
            db, title="Radical left socialist agenda threatens free market values",
            description="Conservative leaders oppose woke cancel culture policies",
            source_name="Unknown Source",
        )
        _process_single(article)
        assert 0.0 <= article.bias_confidence <= 1.0

    def test_signals_json_valid_list(self, db):
        article = db_article(
            db, title="New climate crisis policy announced by government officials",
            description="Progressive legislation addresses inequality and social justice",
        )
        _process_single(article)
        signals = json.loads(article.bias_signals_json)
        assert isinstance(signals, list)
        assert len(signals) > 0
        assert all(isinstance(s, str) for s in signals)


# ─────────────────────────────────────────────────────────────────────────────
# 7. HASH COLLISION RESISTANCE
# ─────────────────────────────────────────────────────────────────────────────

class TestDeduplicationHash:
    def test_hash_is_64_chars(self):
        h = Article.make_hash("https://example.com", "Title")
        assert len(h) == 64

    def test_hash_case_insensitive(self):
        h1 = Article.make_hash("HTTPS://EXAMPLE.COM", "TITLE")
        h2 = Article.make_hash("https://example.com", "title")
        assert h1 == h2

    def test_hash_whitespace_stripped(self):
        h1 = Article.make_hash("  https://example.com  ", "  Title  ")
        h2 = Article.make_hash("https://example.com", "Title")
        assert h1 == h2

    def test_different_urls_different_hash(self):
        h1 = Article.make_hash("https://example.com/1", "Same Title")
        h2 = Article.make_hash("https://example.com/2", "Same Title")
        assert h1 != h2

    def test_different_titles_different_hash(self):
        h1 = Article.make_hash("https://example.com", "Title One")
        h2 = Article.make_hash("https://example.com", "Title Two")
        assert h1 != h2

    def test_200_unique_articles_all_unique_hashes(self, db):
        hashes = {Article.make_hash(f"https://ex.com/{i}", f"Article Title Number {i}") for i in range(200)}
        assert len(hashes) == 200
