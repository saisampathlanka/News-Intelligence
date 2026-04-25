import feedparser
import bleach
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List
from backend.models.schemas import RawArticle

logger = logging.getLogger("news_intel.rss")


def _parse_date(entry) -> datetime:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return datetime.now(timezone.utc)


def _clean(text: str) -> str:
    if not text:
        return ""
    return bleach.clean(text, tags=[], strip=True).strip()


def fetch_rss(feed_url: str, max_items: int = 50) -> List[RawArticle]:
    """Parse a single RSS/Atom feed URL. Returns list of RawArticle."""
    articles = []
    try:
        feed = feedparser.parse(feed_url)

        if feed.bozo and not feed.entries:
            logger.warning("Feed parse error %s: %s", feed_url, feed.bozo_exception)
            return []

        source_name = feed.feed.get("title", feed_url.split("/")[2])

        for entry in feed.entries[:max_items]:
            url = entry.get("link", "").strip()
            title = _clean(entry.get("title", ""))
            if not url or not title:
                continue

            # Prefer summary, fallback to content
            description = _clean(entry.get("summary", ""))
            content_blob = entry.get("content", [{}])
            content = _clean(
                content_blob[0].get("value", "") if content_blob else description
            )

            articles.append(RawArticle(
                url=url,
                title=title,
                description=description[:500],
                content=content[:5000],
                source_name=source_name,
                source_type="rss",
                published_at=_parse_date(entry),
                author=_clean(entry.get("author", "")),
                language=feed.feed.get("language", "en")[:5],
            ))

        logger.info("RSS %s → %d articles", source_name, len(articles))

    except Exception as e:
        logger.error("RSS fetch failed [%s]: %s", feed_url, e)

    return articles


def fetch_all_rss(feed_urls: List[str], max_per_feed: int = 50) -> List[RawArticle]:
    results = []
    for url in feed_urls:
        results.extend(fetch_rss(url, max_per_feed))
    return results
