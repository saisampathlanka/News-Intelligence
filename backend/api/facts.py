"""
Fact Intersection API endpoints.
Surfaces story clusters, common facts, and conflicting claims.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from backend.core.auth_deps import require_auth
from backend.models.user import User
from sqlalchemy.orm import Session

from backend.core.database import get_db
from backend.services.fact_engine import (
    build_story_clusters,
    fetch_articles_for_clustering,
)

router = APIRouter(prefix="/facts", tags=["Fact Intersection"])


@router.get("/clusters")
def get_story_clusters(
    _user: User = Depends(require_auth),
    hours: int = Query(default=24, ge=1, le=168, description="Lookback window in hours"),
    topic: Optional[str] = Query(default=None, description="Filter by topic"),
    min_sources: int = Query(default=2, ge=2, le=10, description="Min sources per cluster"),
    db: Session = Depends(get_db),
):
    """
    Cluster recent articles into stories covered by multiple sources.

    Returns clusters with:
    - **common_facts**: claims corroborated by ≥55% of sources
    - **conflicts**: numeric divergence or contradictory outcome verbs
    - **sources**: which outlets covered this story
    """
    articles = fetch_articles_for_clustering(db, hours=hours, topic=topic)
    if not articles:
        return {"clusters": [], "total_articles": 0, "window_hours": hours}

    clusters = build_story_clusters(articles, min_cluster_size=min_sources)

    return {
        "clusters": [c.to_dict() for c in clusters],
        "total_clusters": len(clusters),
        "total_articles": len(articles),
        "window_hours": hours,
    }


@router.get("/clusters/{cluster_id}")
def get_cluster_detail(
    cluster_id: str,
    hours: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
):
    """Get detailed analysis for a specific story cluster."""
    articles = fetch_articles_for_clustering(db, hours=hours)
    clusters = build_story_clusters(articles)

    match = next((c for c in clusters if c.cluster_id == cluster_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Cluster '{cluster_id}' not found")

    return match.to_dict()


@router.get("/conflicts")
def get_all_conflicts(
    hours: int = Query(default=24, ge=1, le=72),
    severity: Optional[str] = Query(default=None, description="low | medium | high"),
    db: Session = Depends(get_db),
):
    """
    Return all detected conflicting claims across story clusters.
    Useful for surfacing contested facts and media divergence.
    """
    articles = fetch_articles_for_clustering(db, hours=hours)
    clusters = build_story_clusters(articles)

    all_conflicts = []
    for cluster in clusters:
        for conflict in cluster.conflicts:
            conflict["cluster_id"] = cluster.cluster_id
            conflict["story_sources"] = cluster.sources
            if severity is None or conflict.get("severity") == severity:
                all_conflicts.append(conflict)

    # Sort by severity
    sev_order = {"high": 0, "medium": 1, "low": 2}
    all_conflicts.sort(key=lambda c: sev_order.get(c.get("severity", "low"), 3))

    return {
        "conflicts": all_conflicts,
        "total": len(all_conflicts),
        "window_hours": hours,
    }


@router.get("/common-facts")
def get_common_facts(
    hours: int = Query(default=24, ge=1, le=72),
    min_confidence: float = Query(default=0.55, ge=0.3, le=1.0),
    topic: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Return facts corroborated by multiple independent sources.
    Higher confidence = mentioned by more outlets in the cluster.
    """
    articles = fetch_articles_for_clustering(db, hours=hours, topic=topic)
    clusters = build_story_clusters(articles)

    all_facts = []
    for cluster in clusters:
        for fact in cluster.common_facts:
            if fact.get("confidence", 0) >= min_confidence:
                fact["cluster_id"] = cluster.cluster_id
                fact["story_sources"] = cluster.sources
                all_facts.append(fact)

    # Sort by confidence desc
    all_facts.sort(key=lambda f: f.get("confidence", 0), reverse=True)

    return {
        "common_facts": all_facts[:50],
        "total": len(all_facts),
        "min_confidence_threshold": min_confidence,
        "window_hours": hours,
    }
