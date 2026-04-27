"""
Recommendations API.
"""
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from backend.core.auth_deps import require_auth
from backend.models.user import User
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.api.schemas import RelatedArticleResponse, ArticleBase
from backend.services.recommendations import find_similar_articles, build_topic_graph, get_related_topics

router = APIRouter(prefix="/recommendations", tags=["Recommendations"])


@router.get("/{article_id}", response_model=List[RelatedArticleResponse])
def get_recommendations(
    article_id: int,
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    _user: User = Depends(require_auth),
):
    """
    Get recommended articles similar to the given article.
    
    Uses multiple signals:
    - Topic similarity
    - Entity overlap
    - Keyword matching
    - Diverse perspectives (different bias)
    """
    similar = find_similar_articles(db, article_id, limit=limit)
    
    if not similar:
        raise HTTPException(
            status_code=404,
            detail="Article not found or no recommendations available"
        )
    
    results = []
    for article, score, rel_type in similar:
        results.append(RelatedArticleResponse(
            article=ArticleBase.model_validate(article),
            similarity_score=round(score, 2),
            relation_type=rel_type,
        ))
    
    return results


@router.get("/topic/{topic}", response_model=List[ArticleBase])
def get_topic_recommendations(
    topic: str,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    _user: User = Depends(require_auth),
):
    """Get recent articles for a specific topic."""
    from backend.models.article import Article
    from sqlalchemy import select, desc
    
    query = (
        select(Article)
        .where(Article.topic == topic)
        .where(Article.is_processed == True)
        .order_by(desc(Article.published_at))
        .limit(limit)
    )
    
    articles = db.execute(query).scalars().all()
    
    return [ArticleBase.model_validate(a) for a in articles]


@router.get("/topics/graph")
def get_topic_graph(
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(require_auth),
):
    """
    Topic co-occurrence graph.

    Returns nodes (topics) and edges (co-occurrence frequency).
    Use this to understand which topics are linked — e.g. technology and business
    articles frequently co-mention the same entities.
    """
    graph = build_topic_graph(db)
    return graph.to_dict()


@router.get("/topics/{topic}/related")
def get_related_topics_endpoint(
    request: Request,
    topic: str,
    top_n: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    _user: User = Depends(require_auth),
):
    """
    Get topics related to a given topic via co-occurrence graph.
    Returns topics with co-occurrence weight — higher weight = more related.
    """
    related = get_related_topics(db, topic=topic, top_n=top_n)
    return {
        "topic": topic,
        "related": related,
        "count": len(related),
    }
