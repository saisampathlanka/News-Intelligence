import httpx
import logging
from datetime import datetime, timezone
from typing import List, Optional
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from backend.models.schemas import RawArticle

logger = logging.getLogger("news_intel.api_fetcher")

TIMEOUT = httpx.Timeout(10.0)

def _retry():
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.ConnectError)),
        reraise=True,
    )


def _parse_iso(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


# ── NewsAPI ──────────────────────────────────────────────────────────────────

@_retry()
def fetch_newsapi(api_key: str, query: str = "world", max_items: int = 50) -> List[RawArticle]:
    """https://newsapi.org — 100 req/day free tier"""
    articles = []
    try:
        resp = httpx.get(
            "https://newsapi.org/v2/top-headlines",
            params={"language": "en", "pageSize": min(max_items, 100), "apiKey": api_key},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") != "ok":
            logger.warning("NewsAPI error: %s", data.get("message"))
            return []

        for a in data.get("articles", []):
            url = a.get("url", "")
            title = (a.get("title") or "").strip()
            if not url or not title or title == "[Removed]":
                continue
            articles.append(RawArticle(
                url=url,
                title=title,
                description=(a.get("description") or "")[:500],
                content=(a.get("content") or "")[:5000],
                source_name=a.get("source", {}).get("name", "NewsAPI"),
                source_type="newsapi",
                published_at=_parse_iso(a.get("publishedAt", "")),
                author=a.get("author"),
                language="en",
            ))

        logger.info("NewsAPI → %d articles", len(articles))

    except httpx.HTTPStatusError as e:
        logger.error("NewsAPI HTTP %s: %s", e.response.status_code, e.response.text[:200])
    except Exception as e:
        logger.error("NewsAPI failed: %s", e)

    return articles


# ── The Guardian ─────────────────────────────────────────────────────────────

@_retry()
def fetch_guardian(api_key: str, max_items: int = 50) -> List[RawArticle]:
    """https://open-platform.theguardian.com — free tier"""
    articles = []
    try:
        resp = httpx.get(
            "https://content.guardianapis.com/search",
            params={
                "api-key": api_key,
                "page-size": min(max_items, 50),
                "show-fields": "trailText,bodyText,byline",
                "order-by": "newest",
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("response", {}).get("results", [])

        for r in results:
            fields = r.get("fields", {})
            articles.append(RawArticle(
                url=r.get("webUrl", ""),
                title=r.get("webTitle", "").strip(),
                description=(fields.get("trailText") or "")[:500],
                content=(fields.get("bodyText") or "")[:5000],
                source_name="The Guardian",
                source_type="guardian",
                published_at=_parse_iso(r.get("webPublicationDate", "")),
                author=fields.get("byline"),
                language="en",
            ))

        logger.info("Guardian → %d articles", len(articles))

    except Exception as e:
        logger.error("Guardian failed: %s", e)

    return articles


# ── GNews ────────────────────────────────────────────────────────────────────

@_retry()
def fetch_gnews(api_key: str, max_items: int = 10) -> List[RawArticle]:
    """https://gnews.io — 100 req/day, 10 articles/req free tier"""
    articles = []
    try:
        resp = httpx.get(
            "https://gnews.io/api/v4/top-headlines",
            params={"token": api_key, "lang": "en", "max": min(max_items, 10)},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()

        for a in resp.json().get("articles", []):
            articles.append(RawArticle(
                url=a.get("url", ""),
                title=(a.get("title") or "").strip(),
                description=(a.get("description") or "")[:500],
                content=(a.get("content") or "")[:5000],
                source_name=a.get("source", {}).get("name", "GNews"),
                source_type="gnews",
                published_at=_parse_iso(a.get("publishedAt", "")),
                author=None,
                language="en",
            ))

        logger.info("GNews → %d articles", len(articles))

    except Exception as e:
        logger.error("GNews failed: %s", e)

    return articles
