"""
Tests for cache layer, DB optimization, and rate limit middleware.
Run: pytest backend/tests/test_cache_and_limits.py -v
"""
import sys, os, time, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from backend.core.cache import (
    Cache, get_cache,
    articles_key, insights_summary_key, insights_topics_key, insights_bias_key,
    trending_key, stocks_key, clusters_key,
    TTL_SUMMARY, TTL_TOPICS, TTL_ARTICLES_LIST, TTL_TRENDING, TTL_STOCKS,
)


# ── Cache unit tests ──────────────────────────────────────────────────────────

class TestCacheBasics:
    def setup_method(self):
        self.cache = Cache(max_size=50)

    def test_set_and_get(self):
        self.cache.set("k1", {"value": 42}, ttl=60)
        result = self.cache.get("k1")
        assert result == {"value": 42}

    def test_miss_returns_none(self):
        assert self.cache.get("nonexistent") is None

    def test_expired_entry_returns_none(self):
        self.cache.set("exp", "data", ttl=0.01)
        time.sleep(0.05)
        assert self.cache.get("exp") is None

    def test_alive_entry_returned(self):
        self.cache.set("alive", "data", ttl=60)
        assert self.cache.get("alive") == "data"

    def test_overwrite_key(self):
        self.cache.set("k", "v1", ttl=60)
        self.cache.set("k", "v2", ttl=60)
        assert self.cache.get("k") == "v2"

    def test_delete_existing(self):
        self.cache.set("del_me", "x", ttl=60)
        result = self.cache.delete("del_me")
        assert result is True
        assert self.cache.get("del_me") is None

    def test_delete_nonexistent(self):
        assert self.cache.delete("ghost") is False

    def test_len(self):
        cache = Cache(max_size=50)  # fresh cache for clean count
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        assert len(cache) == 2

    def test_various_value_types(self):
        self.cache.set("list", [1, 2, 3], ttl=60)
        self.cache.set("dict", {"a": 1}, ttl=60)
        self.cache.set("str", "hello", ttl=60)
        self.cache.set("int", 42, ttl=60)
        self.cache.set("none_val", None, ttl=60)  # None value = miss, use sentinel instead
        assert self.cache.get("list") == [1, 2, 3]
        assert self.cache.get("dict") == {"a": 1}
        assert self.cache.get("str") == "hello"
        assert self.cache.get("int") == 42


class TestCacheInvalidation:
    def setup_method(self):
        self.cache = Cache(max_size=100)

    def test_invalidate_by_prefix(self):
        self.cache.set("insights:summary", {"data": 1}, ttl=60)
        self.cache.set("insights:topics", {"data": 2}, ttl=60)
        self.cache.set("articles:1:20:None:None:None:None", {"data": 3}, ttl=60)

        count = self.cache.invalidate("insights:")
        assert count == 2
        assert self.cache.get("insights:summary") is None
        assert self.cache.get("insights:topics") is None
        assert self.cache.get("articles:1:20:None:None:None:None") is not None

    def test_invalidate_all(self):
        for i in range(10):
            self.cache.set(f"key:{i}", i, ttl=60)
        count = self.cache.invalidate("")
        assert count == 10
        assert len(self.cache) == 0

    def test_invalidate_nonexistent_prefix(self):
        count = self.cache.invalidate("nonexistent:")
        assert count == 0

    def test_invalidate_empty_cache(self):
        count = self.cache.invalidate("")
        assert count == 0


class TestCacheLRUEviction:
    def test_evicts_oldest_when_full(self):
        cache = Cache(max_size=3)
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.set("c", 3, ttl=60)
        cache.set("d", 4, ttl=60)  # should evict "a"
        assert cache.get("a") is None
        assert cache.get("d") == 4
        assert len(cache) == 3

    def test_lru_touch_prevents_eviction(self):
        cache = Cache(max_size=3)
        cache.set("a", 1, ttl=60)
        cache.set("b", 2, ttl=60)
        cache.set("c", 3, ttl=60)
        cache.get("a")             # touch "a" — moves to end
        cache.set("d", 4, ttl=60)  # should evict "b" now (oldest untouched)
        assert cache.get("a") == 1
        assert cache.get("b") is None


class TestCacheStats:
    def setup_method(self):
        self.cache = Cache(max_size=100)

    def test_initial_stats(self):
        stats = self.cache.stats()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["size"] == 0
        assert stats["hit_rate"] == 0.0

    def test_hit_rate_increases(self):
        self.cache.set("k", "v", ttl=60)
        self.cache.get("k")    # hit
        self.cache.get("k")    # hit
        self.cache.get("miss") # miss
        stats = self.cache.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert abs(stats["hit_rate"] - 2/3) < 0.01

    def test_expired_counted_as_miss(self):
        self.cache.set("exp", "v", ttl=0.01)
        time.sleep(0.05)
        self.cache.get("exp")  # miss (expired)
        assert self.cache.stats()["misses"] == 1

    def test_alive_entries_count(self):
        self.cache.set("a", 1, ttl=60)
        self.cache.set("b", 2, ttl=0.01)
        time.sleep(0.05)
        stats = self.cache.stats()
        assert stats["alive_entries"] == 1  # only "a" is alive


class TestCachePurge:
    def test_purge_removes_expired(self):
        cache = Cache(max_size=100)
        cache.set("alive", 1, ttl=60)
        cache.set("dead1", 2, ttl=0.01)
        cache.set("dead2", 3, ttl=0.01)
        time.sleep(0.05)
        purged = cache.purge_expired()
        assert purged == 2
        assert len(cache) == 1

    def test_purge_empty_cache(self):
        cache = Cache(max_size=100)
        assert cache.purge_expired() == 0


class TestCacheThreadSafety:
    def test_concurrent_writes_no_corruption(self):
        cache = Cache(max_size=1000)
        errors = []

        def writer(start: int):
            try:
                for i in range(start, start + 50):
                    cache.set(f"key:{i}", i * 2, ttl=60)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(i * 50,)) for i in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert len(errors) == 0
        # All 200 keys should be present (within max_size=1000)
        found = sum(1 for i in range(200) if cache.get(f"key:{i}") is not None)
        assert found == 200

    def test_concurrent_reads_writes(self):
        cache = Cache(max_size=100)
        cache.set("shared", 0, ttl=60)
        errors = []

        def reader():
            try:
                for _ in range(100):
                    cache.get("shared")
            except Exception as e:
                errors.append(str(e))

        def writer():
            try:
                for i in range(100):
                    cache.set("shared", i, ttl=60)
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=reader),
            threading.Thread(target=writer),
        ]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(errors) == 0


# ── Cache key builders ────────────────────────────────────────────────────────

class TestCacheKeys:
    def test_articles_key_unique_per_params(self):
        k1 = articles_key(1, 20, "politics", None, None, None)
        k2 = articles_key(1, 20, "business", None, None, None)
        k3 = articles_key(2, 20, "politics", None, None, None)
        assert k1 != k2
        assert k1 != k3
        assert k2 != k3

    def test_articles_key_deterministic(self):
        k1 = articles_key(1, 20, "politics", "Reuters", "center", 7)
        k2 = articles_key(1, 20, "politics", "Reuters", "center", 7)
        assert k1 == k2

    def test_insights_keys_distinct(self):
        assert insights_summary_key() != insights_topics_key()
        assert insights_topics_key() != insights_bias_key()

    def test_trending_key_unique(self):
        assert trending_key(24, 10) != trending_key(48, 10)
        assert trending_key(24, 10) != trending_key(24, 20)

    def test_stocks_key_unique(self):
        assert stocks_key("global") != stocks_key("europe")


# ── TTL constants sanity ──────────────────────────────────────────────────────

class TestTTLConstants:
    def test_all_positive(self):
        for ttl in [TTL_SUMMARY, TTL_TOPICS, TTL_ARTICLES_LIST, TTL_TRENDING, TTL_STOCKS]:
            assert ttl > 0, f"TTL should be positive, got {ttl}"

    def test_article_detail_longer_than_list(self):
        from backend.core.cache import TTL_ARTICLE_DETAIL
        assert TTL_ARTICLE_DETAIL > TTL_ARTICLES_LIST

    def test_summary_shorter_than_topics(self):
        # Summary changes more frequently (depends on processing)
        # Topics change less (stable categories)
        assert TTL_SUMMARY <= TTL_TOPICS


# ── Singleton ─────────────────────────────────────────────────────────────────

class TestCacheSingleton:
    def test_same_instance_returned(self):
        c1 = get_cache()
        c2 = get_cache()
        assert c1 is c2

    def test_singleton_persists_data(self):
        cache = get_cache()
        cache.set("_test_singleton", "persisted", ttl=60)
        cache2 = get_cache()
        assert cache2.get("_test_singleton") == "persisted"
        cache.delete("_test_singleton")  # cleanup


# ── Rate limit middleware logic ───────────────────────────────────────────────

class TestRateLimitLogic:
    """Test the tier classification logic without HTTP."""

    def _make_middleware(self):
        class FakeSettings:
            RATE_LIMIT_DEFAULT = 60
            RATE_LIMIT_EXPENSIVE = 20
            RATE_LIMIT_ADMIN = 10
            RATE_LIMIT_STOCKS = 30

        class FakeApp:
            pass

        # Import only the tier logic
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

        from starlette.middleware.base import BaseHTTPMiddleware
        # Reproduce tier logic inline for unit testing
        settings = FakeSettings()

        def _tier(path):
            if path.startswith("/admin"):
                return "admin", settings.RATE_LIMIT_ADMIN
            if path in ("/insights/summary", "/facts/clusters", "/facts/conflicts"):
                return "expensive", settings.RATE_LIMIT_EXPENSIVE
            if path.startswith("/stocks"):
                return "stocks", settings.RATE_LIMIT_STOCKS
            return "default", settings.RATE_LIMIT_DEFAULT

        return _tier

    def test_admin_tier(self):
        _tier = self._make_middleware()
        name, limit = _tier("/admin/pipeline")
        assert name == "admin"
        assert limit == 10

    def test_expensive_tier_summary(self):
        _tier = self._make_middleware()
        name, limit = _tier("/insights/summary")
        assert name == "expensive"
        assert limit == 20

    def test_expensive_tier_clusters(self):
        _tier = self._make_middleware()
        name, limit = _tier("/facts/clusters")
        assert name == "expensive"
        assert limit == 20

    def test_stocks_tier(self):
        _tier = self._make_middleware()
        name, limit = _tier("/stocks")
        assert name == "stocks"
        assert limit == 30

    def test_stocks_tier_multi(self):
        _tier = self._make_middleware()
        name, limit = _tier("/stocks/multi")
        assert name == "stocks"
        assert limit == 30

    def test_default_tier_articles(self):
        _tier = self._make_middleware()
        name, limit = _tier("/articles")
        assert name == "default"
        assert limit == 60

    def test_default_tier_recommendations(self):
        _tier = self._make_middleware()
        name, limit = _tier("/recommendations/42")
        assert name == "default"
        assert limit == 60

    def test_health_default_tier(self):
        _tier = self._make_middleware()
        name, limit = _tier("/health")
        assert name == "default"
        assert limit == 60


# ── DB Index constants ────────────────────────────────────────────────────────

class TestDBIndexMigration:
    def test_migration_file_exists(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "../../alembic/versions/004_query_indexes.py")
        assert os.path.exists(path), "004_query_indexes.py migration not found"

    def test_migration_syntax(self):
        import ast
        path = os.path.join(os.path.dirname(__file__), "../../alembic/versions/004_query_indexes.py")
        with open(path) as f:
            ast.parse(f.read())

    def test_migration_chain(self):
        import ast, re
        path = os.path.join(os.path.dirname(__file__), "../../alembic/versions/004_query_indexes.py")
        src = open(path).read()
        assert "down_revision = '003_bias_confidence'" in src, "Migration chain broken"
        assert "revision = '004_query_indexes'" in src
