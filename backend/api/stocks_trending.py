"""
Stocks and Trending API endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.services.stock_service import get_market_summary, fetch_multi_region, REGIONAL_INDICES
from backend.services.trending_service import (
    get_trending_topics,
    get_trending_entities,
    get_cross_source_stories,
)
from backend.core.cache import get_cache, trending_key, stocks_key, TTL_TRENDING, TTL_STOCKS
from config.settings import settings

stocks_router = APIRouter(prefix="/stocks", tags=["Markets"])
trending_router = APIRouter(prefix="/trending", tags=["Trending"])


# ── STOCKS ────────────────────────────────────────────────────────────────────

@stocks_router.get("")
def get_stocks(
    request: Request,
    region: str = Query(default=None, description="Region code. Defaults to USER_REGION setting."),
):
    """
    Get stock market data for a region.

    Available regions: global, west, europe, middle_east, india,
                       southeast_asia, east_asia, africa, latin_america
    """
    region = region or settings.USER_REGION
    if region not in REGIONAL_INDICES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown region '{region}'. Valid: {list(REGIONAL_INDICES.keys())}"
        )

    cache = get_cache()
    key = stocks_key(region)
    cached = cache.get(key)
    if cached is not None:
        return cached

    result = get_market_summary(region)
    ttl = settings.CACHE_TTL_STOCKS
    if ttl > 0:
        cache.set(key, result, ttl)
    return result


@stocks_router.get("/multi")
def get_multi_region_stocks(
    regions: str = Query(
        default="global,west,europe",
        description="Comma-separated region codes"
    ),
):
    """Get stock market data for multiple regions at once."""
    region_list = [r.strip() for r in regions.split(",") if r.strip()]
    invalid = [r for r in region_list if r not in REGIONAL_INDICES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown regions: {invalid}")

    return fetch_multi_region(region_list)


@stocks_router.get("/regions")
def list_regions():
    """List all available regions with their tracked indices."""
    return {
        region: [
            {"ticker": t, "name": n, "currency": c}
            for t, n, _, c in indices
        ]
        for region, indices in REGIONAL_INDICES.items()
    }


# ── TRENDING ──────────────────────────────────────────────────────────────────

@trending_router.get("")
def get_trending(
    request: Request,
    hours: int = Query(default=24, ge=1, le=168, description="Lookback window in hours"),
    top_n: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """
    Get trending topics ranked by article velocity, source diversity, and entity mentions.

    Response includes:
    - score: composite trend score
    - article_count: how many articles in window
    - source_count: how many different outlets covered it
    - is_contested: topic covered with widely varying bias
    - sentiment_label: overall emotional tone
    - sample_headlines: example article titles
    """
    cache = get_cache()
    key = trending_key(hours, top_n)
    cached = cache.get(key)
    if cached is not None:
        return cached

    topics = get_trending_topics(db, hours=hours, top_n=top_n)
    result = {"trending": topics, "window_hours": hours, "total_topics": len(topics)}
    ttl = settings.CACHE_TTL_TRENDING
    if ttl > 0:
        cache.set(key, result, ttl)
    return result


@trending_router.get("/entities")
def get_trending_entities_endpoint(
    hours: int = Query(default=24, ge=1, le=168),
    top_n: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Get the most-mentioned people, organizations, and places in recent news.
    """
    return get_trending_entities(db, hours=hours, top_n=top_n)


@trending_router.get("/corroborated")
def get_corroborated_stories(
    hours: int = Query(default=12, ge=1, le=72),
    min_sources: int = Query(default=3, ge=2, le=10),
    db: Session = Depends(get_db),
):
    """
    Get stories covered by multiple independent sources — higher credibility signal.
    min_sources: minimum number of different outlets to include a story.
    """
    stories = get_cross_source_stories(db, hours=hours, min_sources=min_sources)
    return {
        "stories": stories,
        "window_hours": hours,
        "min_sources_threshold": min_sources,
    }
