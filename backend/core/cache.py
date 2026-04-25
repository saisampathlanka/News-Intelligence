"""
In-memory TTL cache for API responses.

Design:
  - Pure Python stdlib — no Redis, no external deps
  - Thread-safe via threading.Lock
  - Per-key TTL (different endpoints can have different expiry)
  - LRU eviction when max_size reached
  - Cache.invalidate(prefix) for targeted invalidation after pipeline runs

Usage:
    cache = get_cache()
    val = cache.get("insights:summary")
    if val is None:
        val = compute_expensive_thing()
        cache.set("insights:summary", val, ttl=60)
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger("news_intel.cache")

# ── Default TTLs (seconds) ────────────────────────────────────────────────────
TTL_SUMMARY       = 60     # /insights/summary       — 1 minute
TTL_TOPICS        = 120    # /insights/topics         — 2 minutes
TTL_BIAS_DIST     = 120    # /insights/bias-distribution
TTL_ARTICLES_LIST = 30     # /articles (paginated)    — 30 seconds
TTL_ARTICLE_DETAIL = 300   # /articles/{id}           — 5 minutes (rarely changes)
TTL_TRENDING      = 90     # /trending                — 90 seconds
TTL_STOCKS        = 60     # /stocks                  — 1 minute
TTL_CLUSTERS      = 120    # /facts/clusters          — 2 minutes


# ── Cache Entry ───────────────────────────────────────────────────────────────

class _Entry:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, ttl: float):
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def is_alive(self) -> bool:
        return time.monotonic() < self.expires_at


# ── Cache ─────────────────────────────────────────────────────────────────────

class Cache:
    """
    Thread-safe LRU cache with per-entry TTL.

    Keys are strings. Values can be any JSON-serializable object.
    """

    def __init__(self, max_size: int = 512):
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[Any]:
        """Return cached value or None if missing/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if not entry.is_alive:
                del self._store[key]
                self._misses += 1
                return None
            # Move to end (LRU touch)
            self._store.move_to_end(key)
            self._hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: float) -> None:
        """Store value with TTL seconds expiry."""
        with self._lock:
            self._store[key] = _Entry(value, ttl)
            self._store.move_to_end(key)
            # Evict oldest if over capacity
            while len(self._store) > self._max_size:
                oldest = next(iter(self._store))
                del self._store[oldest]
                logger.debug("Cache evicted key: %s", oldest)

    def invalidate(self, prefix: str = "") -> int:
        """
        Delete all keys matching prefix.
        Returns count of deleted entries.

        Examples:
            cache.invalidate("insights:")   # clear all insight caches
            cache.invalidate("")            # clear everything
        """
        with self._lock:
            if not prefix:
                count = len(self._store)
                self._store.clear()
                logger.info("Cache cleared: %d entries removed", count)
                return count

            to_delete = [k for k in self._store if k.startswith(prefix)]
            for k in to_delete:
                del self._store[k]
            if to_delete:
                logger.debug("Cache invalidated %d keys with prefix '%s'", len(to_delete), prefix)
            return len(to_delete)

    def delete(self, key: str) -> bool:
        """Delete a specific key. Returns True if it existed."""
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            alive = sum(1 for e in self._store.values() if e.is_alive)
            return {
                "size": len(self._store),
                "alive_entries": alive,
                "max_size": self._max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 3) if total > 0 else 0.0,
            }

    def purge_expired(self) -> int:
        """Remove all expired entries. Called by scheduler job."""
        with self._lock:
            dead = [k for k, e in self._store.items() if not e.is_alive]
            for k in dead:
                del self._store[k]
            if dead:
                logger.debug("Cache purged %d expired entries", len(dead))
            return len(dead)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


# ── Singleton ─────────────────────────────────────────────────────────────────

_cache_instance: Optional[Cache] = None
_cache_lock = threading.Lock()


def get_cache() -> Cache:
    """Return the application-wide cache singleton."""
    global _cache_instance
    if _cache_instance is None:
        with _cache_lock:
            if _cache_instance is None:
                _cache_instance = Cache(max_size=512)
                logger.info("Cache initialized (max_size=512)")
    return _cache_instance


# ── Cache key builders ────────────────────────────────────────────────────────

def articles_key(page: int, page_size: int, topic: Optional[str],
                 source: Optional[str], bias_label: Optional[str],
                 days: Optional[int]) -> str:
    return f"articles:{page}:{page_size}:{topic}:{source}:{bias_label}:{days}"


def insights_summary_key() -> str:
    return "insights:summary"


def insights_topics_key() -> str:
    return "insights:topics"


def insights_bias_key() -> str:
    return "insights:bias_dist"


def trending_key(hours: int, top_n: int) -> str:
    return f"trending:{hours}:{top_n}"


def stocks_key(region: str) -> str:
    return f"stocks:{region}"


def clusters_key(hours: int, topic: Optional[str], min_sources: int) -> str:
    return f"clusters:{hours}:{topic}:{min_sources}"
