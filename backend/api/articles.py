"""
Article endpoints: list, search, detail.
"""
import json
from typing import Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, func, desc

from backend.core.database import get_db
from backend.models.article import Article
from backend.api.schemas import ArticleList, ArticleDetail, ArticleBase
from backend.core.cache import get_cache, articles_key, TTL_ARTICLES_LIST, TTL_ARTICLE_DETAIL

router = APIRouter(prefix="/articles", tags=["Articles"])


@router.get("", response_model=ArticleList)
def list_articles(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    topic: Optional[str] = None,
    source: Optional[str] = None,
    bias_label: Optional[str] = None,
    days: Optional[int] = Query(None, description="Articles from last N days"),
    db: Session = Depends(get_db),
):
    """
    List articles with filtering and pagination.
    Cached per unique filter combination for CACHE_TTL_ARTICLES seconds.

    Filters:
    - topic: Filter by topic category
    - source: Filter by source name
    - bias_label: Filter by bias (left, center-left, center, center-right, right)
    - days: Show articles from last N days
    """
    from config.settings import settings
    cache = get_cache()
    cache_key = articles_key(page, page_size, topic, source, bias_label, days)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    query = select(Article).where(Article.is_processed == True)
    
    # Apply filters
    if topic:
        query = query.where(Article.topic == topic)
    if source:
        query = query.where(Article.source_name == source)
    if bias_label:
        query = query.where(Article.bias_label == bias_label)
    if days:
        cutoff = datetime.utcnow() - timedelta(days=days)
        query = query.where(Article.published_at >= cutoff)
    
    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()
    
    # Paginate
    query = query.order_by(desc(Article.published_at))
    query = query.offset((page - 1) * page_size).limit(page_size)
    
    articles = db.execute(query).scalars().all()
    
    result = ArticleList(
        articles=[ArticleBase.model_validate(a) for a in articles],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )
    ttl = settings.CACHE_TTL_ARTICLES
    if ttl > 0:
        cache.set(cache_key, result, ttl)
    return result


@router.get("/{article_id}", response_model=ArticleDetail)
def get_article(article_id: int, db: Session = Depends(get_db)):
    """Get detailed article by ID."""
    article = db.query(Article).filter(Article.id == article_id).first()
    
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    
    # Parse JSON fields
    entities = json.loads(article.entities_json) if article.entities_json else None
    keywords = json.loads(article.keywords_json) if article.keywords_json else None
    bias_signals = json.loads(article.bias_signals_json) if getattr(article, 'bias_signals_json', None) else None

    # Build response
    data = ArticleDetail.model_validate(article)
    data.entities = entities
    data.keywords = keywords
    data.bias_signals = bias_signals

    return data


@router.get("/search/query")
def search_articles(
    q: str = Query(..., min_length=2),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Simple keyword search in title and description.
    Production: use full-text search or Elasticsearch.
    """
    search = f"%{q}%"
    query = (
        select(Article)
        .where(Article.is_processed == True)
        .where(
            (Article.title.ilike(search)) | (Article.description.ilike(search))
        )
    )
    
    count_query = select(func.count()).select_from(query.subquery())
    total = db.execute(count_query).scalar()
    
    query = query.order_by(desc(Article.published_at))
    query = query.offset((page - 1) * page_size).limit(page_size)
    
    articles = db.execute(query).scalars().all()
    
    return ArticleList(
        articles=[ArticleBase.model_validate(a) for a in articles],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )
