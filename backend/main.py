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
        if "server" in response.headers:
            del response.headers["server"]
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
    try:
        from backend.core.database import SessionLocal
        session = SessionLocal()
        try:
            email = settings.INITIAL_ADMIN_EMAIL.strip().lower()
            admin = session.query(_U).filter(_U.email == email).first()
            
            if not admin:
                # Create new admin
                admin = _U(
                    email=email,
                    hashed_password=hash_password(settings.INITIAL_ADMIN_PASSWORD),
                    role="admin",
                    is_active=True,
                )
                session.add(admin)
                session.commit()
                logger.info("Seeded new admin user: %s", email)
            elif admin.role != "admin":
                # Promote existing user to admin
                admin.role = "admin"
                session.commit()
                logger.info("Promoted existing user to admin: %s", email)
        finally:
            session.close()
    except Exception as e:
        logger.warning("Admin seed skipped: %s", e)
    finally:
        if db: db.close()


# ── Safe global exception handler ────────────────────────────────────────────
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from starlette.exceptions import HTTPException as StarletteHTTPException

@app.exception_handler(StarletteHTTPException)
@app.exception_handler(HTTPException)
@app.exception_handler(404)
@app.exception_handler(405)
async def http_exception_handler(request, exc):
    """Custom handler for HTTP errors (404, 403, etc) with clean HTML fallback."""
    accept = request.headers.get("accept", "")
    status_code = getattr(exc, "status_code", 404)
    
    # Return HTML if browser-like client
    if "text/html" in accept:
        # Determine icon/title based on status
        if status_code == 404:
            icon, title = "🔍", "Page Not Found"
        elif status_code in (401, 403):
            icon, title = "🔒", "Access Denied"
        elif status_code == 405:
            icon, title = "🚫", "Method Not Allowed"
        else:
            icon, title = "⚠️", f"Error {status_code}"
            
        msg = getattr(exc, "detail", "The requested resource could not be found.")
        
        html = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{exc.status_code} | NewsIntel</title>
            <style>
                body {{ background: #0a0a0c; color: #e1e1e6; font-family: 'Inter', system-ui, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
                .card {{ text-align: center; background: #141417; padding: 3rem; border-radius: 1.5rem; border: 1px solid #2a2a2e; box-shadow: 0 20px 40px rgba(0,0,0,0.4); max-width: 400px; width: 90%; }}
                .icon {{ font-size: 5rem; margin-bottom: 1.5rem; display: block; }}
                h1 {{ font-size: 1.5rem; font-weight: 700; margin: 0 0 1rem; color: #fff; }}
                p {{ color: #a1a1aa; font-size: 0.95rem; line-height: 1.6; margin: 0 0 2rem; }}
                .btn {{ background: #c29d5f; color: #000; padding: 0.8rem 1.5rem; border-radius: 0.6rem; text-decoration: none; font-weight: 600; font-size: 0.9rem; transition: transform 0.2s; display: inline-block; }}
                .btn:hover {{ transform: translateY(-2px); }}
            </style>
        </head>
        <body>
            <div class="card">
                <span class="icon">{icon}</span>
                <h1>{title}</h1>
                <p>{msg}</p>
                <a href="/" class="btn">Back to Home</a>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html, status_code=status_code)
    
    # Fallback to JSON
    return JSONResponse(status_code=status_code, content={"detail": getattr(exc, "detail", "Not Found")})

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

    # Trigger initial data fetch if DB is empty (so dashboard isn't blank on first load)
    _trigger_initial_fetch()


def _trigger_initial_fetch():
    """Run ingestion, processing and stock refresh in background on first start."""
    from backend.core.database import SessionLocal
    from backend.models.article import Article
    db = SessionLocal()
    # Trigger if DB is empty OR if there are unprocessed articles (ensures data is ready)
    try:
        count = db.query(Article).count()
        unprocessed = db.query(Article).filter(Article.is_processed == False).count()
        
        if count == 0 or unprocessed > 0:
            logger.info("Triggering initial fetch/process (count=%d, unprocessed=%d)", count, unprocessed)
            from backend.core.scheduler import _ingest_job, _process_job, _stock_refresh_job
            import threading
            def run_init():
                if count == 0:
                    _ingest_job()
                _process_job()
                _stock_refresh_job()
            threading.Thread(target=run_init, daemon=True).start()
    except Exception as e:
        logger.warning("Could not trigger initial fetch: %s", e)
    finally:
        db.close()


@app.on_event("shutdown")
async def shutdown():
    """Cleanup on shutdown."""
    from backend.core.scheduler import stop_scheduler
    stop_scheduler()
    logger.info("Shutting down...")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
