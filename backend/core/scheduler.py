"""
APScheduler-based job scheduler.
Jobs:
  1. Ingest news every N minutes
  2. Process unprocessed articles every N+5 minutes
  3. Precompute recommendations every hour
  4. Refresh topic stats every hour
"""
import logging
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore

from backend.core.database import SessionLocal
from backend.services.ingestion import run_ingestion
from backend.services.processing import process_batch
from backend.services.recommendations import precompute_relationships
from config.settings import settings

logger = logging.getLogger("news_intel.scheduler")


def _ingest_job():
    """Ingestion job — fetches and stores new articles."""
    logger.info("[Scheduler] Running ingestion job")
    db = SessionLocal()
    try:
        result = run_ingestion(db)
        logger.info("[Scheduler] Ingestion done: %s", result)
    except Exception as e:
        logger.error("[Scheduler] Ingestion failed: %s", e)
    finally:
        db.close()


def _process_job():
    """Processing job — classifies unprocessed articles."""
    logger.info("[Scheduler] Running processing job")
    db = SessionLocal()
    try:
        result = process_batch(db, batch_size=200)
        logger.info("[Scheduler] Processing done: %s", result)
    except Exception as e:
        logger.error("[Scheduler] Processing failed: %s", e)
    finally:
        db.close()


def _stock_job_cache_refresh():
    """Pre-warm stock cache for configured region."""
    pass  # Cache is populated on-demand via API calls


def _stock_refresh_job():
    """Refresh stock data for user region (logs result)."""
    logger.info("[Scheduler] Refreshing stock data for region=%s", settings.USER_REGION)
    try:
        from backend.services.stock_service import get_market_summary
        summary = get_market_summary(settings.USER_REGION)
        logger.info("[Scheduler] Stocks: mood=%s up=%s down=%s",
                    summary.get("market_mood"), summary.get("up_count"), summary.get("down_count"))
    except Exception as e:
        logger.error("[Scheduler] Stock refresh failed: %s", e)


def _cache_purge_job():
    """Remove expired cache entries — prevents unbounded memory growth."""
    try:
        from backend.core.cache import get_cache
        purged = get_cache().purge_expired()
        if purged:
            logger.info("[Scheduler] Cache: purged %d expired entries", purged)
    except Exception as e:
        logger.error("[Scheduler] Cache purge failed: %s", e)


def _recommendations_job():
    """Precompute article relationships."""
    logger.info("[Scheduler] Running recommendations job")
    db = SessionLocal()
    try:
        count = precompute_relationships(db, batch_size=100)
        logger.info("[Scheduler] Recommendations done: %d articles", count)
    except Exception as e:
        logger.error("[Scheduler] Recommendations failed: %s", e)
    finally:
        db.close()


def create_scheduler() -> BackgroundScheduler:
    """Build and configure scheduler."""
    scheduler = BackgroundScheduler(
        jobstores={"default": MemoryJobStore()},
        job_defaults={
            "coalesce": True,        # don't pile up missed jobs
            "max_instances": 1,      # one instance per job
            "misfire_grace_time": 60,
        },
    )

    interval = settings.FETCH_INTERVAL_MINUTES

    # Ingest every N minutes
    scheduler.add_job(
        _ingest_job,
        trigger=IntervalTrigger(minutes=interval),
        id="ingest",
        name="News Ingestion",
        replace_existing=True,
    )

    # Process 5 minutes after ingest cycle
    scheduler.add_job(
        _process_job,
        trigger=IntervalTrigger(minutes=interval, start_date=_offset_minutes(5)),
        id="process",
        name="Article Processing",
        replace_existing=True,
    )

    # Recommendations every hour
    scheduler.add_job(
        _recommendations_job,
        trigger=IntervalTrigger(hours=1),
        id="recommendations",
        name="Recommendation Precompute",
        replace_existing=True,
    )

    # Stock market data every 15 minutes
    scheduler.add_job(
        _stock_refresh_job,
        trigger=IntervalTrigger(minutes=settings.STOCK_UPDATE_INTERVAL_MINUTES),
        id="stocks",
        name="Stock Market Refresh",
        replace_existing=True,
    )

    # Cache housekeeping every 10 minutes
    scheduler.add_job(
        _cache_purge_job,
        trigger=IntervalTrigger(minutes=10),
        id="cache_purge",
        name="Cache Purge Expired",
        replace_existing=True,
    )

    return scheduler


def _offset_minutes(n: int):
    """Return a future datetime offset by n minutes (for job staggering)."""
    from datetime import timedelta
    return datetime.now() + timedelta(minutes=n)


# Singleton instance
_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = create_scheduler()
    return _scheduler


def start_scheduler():
    """Start the scheduler. Call from app startup."""
    s = get_scheduler()
    if not s.running:
        s.start()
        logger.info(
            "[Scheduler] Started. Jobs: %s",
            [j.id for j in s.get_jobs()],
        )


def stop_scheduler():
    """Stop the scheduler gracefully."""
    s = get_scheduler()
    if s.running:
        s.shutdown(wait=False)
        logger.info("[Scheduler] Stopped")


def get_job_status() -> list[dict]:
    """Return status of all scheduled jobs."""
    s = get_scheduler()
    jobs = []
    for job in s.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "running": s.running,
        })
    return jobs
