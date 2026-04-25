"""
Admin endpoints — protected by require_admin (role=admin only).

All endpoints require:
  Authorization: Bearer <admin_jwt>

Pipeline operations are synchronous for simplicity.
For production high-traffic use, move to a task queue (Celery).
"""
import logging
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from backend.core.auth_deps import require_admin
from backend.core.database import get_db
from backend.core.cache import get_cache
from backend.models.user import User
from backend.api.schemas import IngestionStats, ProcessingStats
from backend.services.ingestion import run_ingestion
from backend.services.processing import process_batch
from backend.services.recommendations import precompute_relationships

router = APIRouter(prefix="/admin", tags=["Admin"])
logger = logging.getLogger("news_intel.admin")


@router.post("/ingest", response_model=IngestionStats)
def trigger_ingestion(
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """
    Manually trigger news ingestion from all configured sources.
    Requires: admin role.
    """
    logger.info(
        "ADMIN_INGEST triggered by user_id=%s ip=%s",
        _user.id, request.client.host if request.client else "unknown",
    )
    result = run_ingestion(db)
    return IngestionStats(
        fetched=result["fetched"],
        saved=result["saved"],
        deduped_in_memory=result["deduped_in_memory"],
        deduped_in_db=result["deduped_in_db"],
        duration_sec=result["duration_sec"],
    )


@router.post("/process", response_model=ProcessingStats)
def trigger_processing(
    request: Request,
    batch_size: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """
    Run NLP processing on unprocessed articles.
    Requires: admin role.
    """
    logger.info(
        "ADMIN_PROCESS triggered by user_id=%s batch=%d",
        _user.id, batch_size,
    )
    result = process_batch(db, batch_size=batch_size)
    return ProcessingStats(
        processed=result["processed"],
        errors=result["errors"],
        duration_sec=result["duration_sec"],
    )


@router.post("/compute-recommendations")
def compute_recommendations(
    request: Request,
    batch_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """
    Precompute article similarity graph.
    Requires: admin role.
    """
    logger.info(
        "ADMIN_RECOMMENDATIONS triggered by user_id=%s batch=%d",
        _user.id, batch_size,
    )
    count = precompute_relationships(db, batch_size=batch_size)
    return {"computed_for": count, "status": "ok"}


@router.post("/pipeline")
def run_full_pipeline(
    request: Request,
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """
    Full pipeline: ingest → process → recommendations → cache invalidation.
    Requires: admin role.
    """
    logger.info(
        "ADMIN_PIPELINE triggered by user_id=%s ip=%s",
        _user.id, request.client.host if request.client else "unknown",
    )

    ingest_result  = run_ingestion(db)
    process_result = process_batch(db, batch_size=200)
    rec_count      = precompute_relationships(db, batch_size=50)

    # Invalidate all caches so next request sees fresh data
    cache = get_cache()
    invalidated = cache.invalidate("")
    logger.info(
        "ADMIN_PIPELINE complete — ingested=%d processed=%d recs=%d cache_cleared=%d",
        ingest_result.get("saved", 0),
        process_result.get("processed", 0),
        rec_count,
        invalidated,
    )

    return {
        "ingestion":               ingest_result,
        "processing":              process_result,
        "recommendations_computed": rec_count,
        "cache_invalidated":       invalidated,
        "status":                  "complete",
    }
