import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backend.core.logging import setup_logging, logger
from backend.core.database import engine, Base
from config.settings import settings

# Import routers
from backend.api import articles, insights, recommendations, admin
from backend.api.stocks_trending import stocks_router, trending_router
from backend.api.facts import router as facts_router
from backend.api.auth import router as auth_router

setup_logging()

# Rate limiter — singleton imported by endpoint modules via: from backend.main import limiter
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="News Intelligence Platform",
    version="0.1.0",
    description="Multi-source news aggregation with bias detection & insights",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS
# ── CORS — origins loaded from ALLOWED_ORIGINS env var ───────────────────────
_allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    allow_credentials=True,
    max_age=600,   # cache preflight 10 minutes
)


# ── Per-endpoint rate limit middleware ────────────────────────────────────────
# Tiered limits applied by path prefix without needing decorators in each module
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse
import time
from collections import defaultdict

# ── Security Headers Middleware ───────────────────────────────────────────────
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every response."""
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"]    = "nosniff"
        response.headers["X-Frame-Options"]           = "DENY"
        response.headers["X-XSS-Protection"]          = "1; mode=block"
        response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]        = "geolocation=(), microphone=(), camera=()"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        # Remove server version leakage
        response.headers.pop("server", None)
        return response

app.add_middleware(SecurityHeadersMiddleware)


class TieredRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding window rate limiter applied by URL path tier.
    Tiers (requests/minute per IP):
      admin  endpoints: 10   — pipeline triggers are expensive
      expensive insights:20  — aggregation queries
      stocks:            30  — external API calls
      default:           60  — all other endpoints
    """
    def __init__(self, app, settings_ref):
        super().__init__(app)
        self._s = settings_ref
        # ip -> {tier: [timestamps]}
        self._windows: dict = defaultdict(lambda: defaultdict(list))
        self._lock = __import__('threading').Lock()

    def _tier(self, path: str) -> tuple:
        """Return (tier_name, max_requests_per_minute) for a path."""
        if path.startswith("/admin"):
            return "admin", self._s.RATE_LIMIT_ADMIN
        if path in ("/insights/summary", "/facts/clusters", "/facts/conflicts"):
            return "expensive", self._s.RATE_LIMIT_EXPENSIVE
        if path.startswith("/stocks"):
            return "stocks", self._s.RATE_LIMIT_STOCKS
        return "default", self._s.RATE_LIMIT_DEFAULT

    async def dispatch(self, request: StarletteRequest, call_next):
        ip = request.client.host if request.client else "unknown"
        path = request.url.path
        tier, limit = self._tier(path)

        now = time.monotonic()
        window_start = now - 60.0  # 60-second window

        with self._lock:
            timestamps = self._windows[ip][tier]
            # Drop timestamps outside window
            self._windows[ip][tier] = [t for t in timestamps if t > window_start]
            count = len(self._windows[ip][tier])

            if count >= limit:
                retry_after = int(60 - (now - self._windows[ip][tier][0])) + 1
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded ({limit}/min for {tier} endpoints). "
                                  f"Retry after {retry_after}s.",
                        "retry_after_seconds": retry_after,
                        "tier": tier,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            self._windows[ip][tier].append(now)

        return await call_next(request)

app.add_middleware(TieredRateLimitMiddleware, settings_ref=settings)


# Serve frontend
_frontend_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'frontend')
try:
    if os.path.isdir(_frontend_path):
        app.mount('/static', StaticFiles(directory=_frontend_path), name='static')
except RuntimeError as _e:
    # aiofiles not installed — StaticFiles unavailable, API still works
    import logging as _log
    _log.getLogger('news_intel').warning('StaticFiles disabled: %s', _e)

@app.get('/dashboard', include_in_schema=False)
def dashboard():
    fp = os.path.join(_frontend_path, 'index.html')
    if os.path.isfile(fp):
        return FileResponse(fp)
    return {'detail': 'Dashboard not available. Install aiofiles or deploy frontend separately.'}

# Include routers
app.include_router(articles.router)
app.include_router(insights.router)
app.include_router(recommendations.router)
app.include_router(admin.router)
app.include_router(stocks_router)
app.include_router(facts_router)
app.include_router(auth_router)
app.include_router(trending_router)


@app.get("/")
def root():
    """API root with available endpoints."""
    return {
        "name": "News Intelligence Platform",
        "version": "0.1.0",
        "endpoints": {
            "articles": "/articles",
            "insights": "/insights",
            "recommendations": "/recommendations",
            "admin": "/admin",
            "docs": "/docs",
        },
    }


@app.get("/health")
def health():
    """Health check endpoint."""
    from sqlalchemy import text
    
    try:
        # Test DB connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "error"
    
    return {
        "status": "ok" if db_status == "ok" else "degraded",
        "version": "0.1.0",
        "database": db_status,
        "environment": settings.ENV,
    }


@app.get("/scheduler/status")
def scheduler_status():
    """Get scheduled job status."""
    from backend.core.scheduler import get_job_status
    return {"jobs": get_job_status()}


@app.get("/cache/stats")
def cache_stats():
    """Cache hit/miss stats and current size."""
    from backend.core.cache import get_cache
    return get_cache().stats()


@app.post("/cache/invalidate")
def cache_invalidate(prefix: str = ""):
    """Manually invalidate cache entries by prefix (or all if prefix is empty)."""
    from backend.core.cache import get_cache
    count = get_cache().invalidate(prefix)
    return {"invalidated": count, "prefix": prefix or "(all)"}


def _seed_admin_user():
    """
    On first run: if INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD are set,
    create the first admin account. Skips if any user already exists.
    """
    if not settings.INITIAL_ADMIN_EMAIL or not settings.INITIAL_ADMIN_PASSWORD:
        return
    from sqlalchemy.orm import Session as _S
    from backend.models.user import User as _U
    from backend.core.security import hash_password
    db = None
    try:
        db = engine.connect()
        from sqlalchemy import text as _t
        count = db.execute(_t("SELECT COUNT(*) FROM users")).scalar()
        if count == 0:
            from backend.core.database import SessionLocal
            session = SessionLocal()
            try:
                admin = _U(
                    email=settings.INITIAL_ADMIN_EMAIL,
                    hashed_password=hash_password(settings.INITIAL_ADMIN_PASSWORD),
                    role="admin",
                    is_active=True,
                )
                session.add(admin)
                session.commit()
                logger.info("Seeded initial admin user: %s", settings.INITIAL_ADMIN_EMAIL)
            finally:
                session.close()
    except Exception as e:
        logger.warning("Admin seed skipped: %s", e)
    finally:
        if db: db.close()


# ── Safe global exception handler ────────────────────────────────────────────
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Catch-all: never expose stack traces to clients."""
    logger.error("UNHANDLED_EXCEPTION path=%s method=%s error=%s",
                 request.url.path, request.method,
                 type(exc).__name__, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Please try again later."},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """Return clean validation errors without internal paths."""
    errors = [
        {"field": ".".join(str(l) for l in e["loc"]), "message": e["msg"]}
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": "Validation error", "errors": errors})


@app.on_event("startup")
async def startup():
    """Initialize app on startup."""
    from backend.core.scheduler import start_scheduler
    from backend.models.article import Article          # noqa: register models
    from backend.models.relations import RelatedArticle, TopicStats
    from backend.models.user import User                # noqa: register user model

    logger.info("News Intelligence Platform starting up...")
    logger.info(f"Environment: {settings.ENV}")
    db_label = settings.DATABASE_URL.split("@")[-1] if "@" in settings.DATABASE_URL else "SQLite"
    logger.info(f"Database: {db_label}")

    # Create tables if they don't exist (safe - skips existing tables)
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables ready")
    except Exception as e:
        logger.error(f"Database init failed: {e}")
        raise  # Fail fast — app is unusable without DB

    # Seed first admin user if credentials provided and no users exist
    _seed_admin_user()

    # Start background scheduler
    start_scheduler()


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    from backend.core.scheduler import stop_scheduler
    stop_scheduler()
    logger.info("Shutting down...")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
