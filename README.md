# NewsIntel — Multi-Source News Intelligence Platform

**v5** · Python 3.11 · FastAPI · PostgreSQL · spaCy · JWT Auth

A production-ready news aggregation platform that ingests 35+ global RSS feeds, applies NLP-based topic classification, political bias detection, and sentiment analysis — then surfaces insights via a REST API and a responsive dashboard.

---

## ✨ What's new in v5

| Module | Change |
|--------|--------|
| **Auth** | JWT + API key authentication on all sensitive endpoints |
| **Bias Detection** | 3-signal engine: source baseline + keyword weights + framing intensity |
| **Frontend** | Decoupled and modularized into app.js and style.css |
| **Stocks** | Integrated Geolocation API to localize market metrics automatically |
| **UX** | Custom UI exception handlers (404, 401) and seamless post-auth auto-refresh |
| **Cache Layer** | In-memory TTL cache — 5-query `/insights/summary` now 2 queries |
| **Rate Limiting** | 4-tier sliding window (10/20/30/60 req/min per IP) |
| **Security** | Security headers, safe error handling, CORS from env |
| **Recommendations** | Weighted 4-signal scoring + topic co-occurrence graph |
| **Tests** | 112 tests across 10 suites — 0 failures |

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/YOUR_USER/news-intel.git
cd news-intel

python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

### 2. Configure environment

```bash
cp .env.example .env
```

Minimum required edits in `.env`:

```ini
# Generate a strong secret:
# python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=<your-64-char-hex-string>

# Create first admin on startup:
INITIAL_ADMIN_EMAIL=admin@example.com
INITIAL_ADMIN_PASSWORD=YourStrongPassword1!
```

### 3. Initialise database

```bash
# Recommended — runs all 5 Alembic migrations in order
alembic upgrade head

# Or auto-create tables (skips migration history)
python scripts/init_db.py
```

### 4. Start the server

```bash
# Development (hot reload)
uvicorn backend.main:app --reload --port 8000

# Production (4 workers, PostgreSQL required)
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
```

### 5. Get a token and call the API

```bash
# Register or login (if INITIAL_ADMIN_* was set, admin is already created)
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"YourStrongPassword1!"}'

# Response:
# {"access_token":"eyJ...","refresh_token":"eyJ...","expires_in":1800}

# Use the token:
curl http://localhost:8000/insights/summary \
  -H "Authorization: Bearer eyJ..."

# Trigger ingestion (admin only):
curl -X POST http://localhost:8000/admin/pipeline \
  -H "Authorization: Bearer eyJ..."

# Open the dashboard:
open http://localhost:8000/dashboard

# Browse API docs:
open http://localhost:8000/docs
```

---

---

## API Overview

All endpoints that return intelligence data require authentication.

| Category | Endpoint | Auth |
|----------|----------|------|
| Auth | `POST /auth/login` | Public |
| Auth | `POST /auth/register` | Public |
| Auth | `POST /auth/refresh` | Public |
| Auth | `GET /auth/me` | ✅ Required |
| Articles | `GET /articles` | Public |
| Articles | `GET /articles/{id}` | Public |
| Insights | `GET /insights/summary` | ✅ Required |
| Insights | `GET /insights/topics` | ✅ Required |
| Trending | `GET /trending` | Public |
| Stocks | `GET /stocks` | Public |
| Facts | `GET /facts/clusters` | ✅ Required |
| Facts | `GET /facts/conflicts` | ✅ Required |
| Recommendations | `GET /recommendations/{id}` | ✅ Required |
| Admin | `POST /admin/pipeline` | ✅ Admin only |

Full documentation: [`docs/API.md`](docs/API.md)

---

## Project Structure

```
news-intel/
├── backend/
│   ├── api/        articles, insights, recommendations, admin,
│   │               facts, auth, stocks_trending
│   ├── core/       database, cache, jwt, security, auth_deps, scheduler, logging
│   ├── models/     article, relations, user, schemas
│   ├── services/   bias_detector, fact_engine, ingestion, nlp_processor,
│   │               processing, recommendations, rss_fetcher, stock_service,
│   │               topic_classifier, trending_service, api_fetchers
│   └── tests/      12 test modules, 112 tests
├── config/
│   └── settings.py
├── alembic/
│   └── versions/   001_initial → 005_users (migration chain)
├── frontend/
│   └── index.html  Self-contained responsive dashboard
├── docs/           API.md, DATABASE.md, DEPLOYMENT.md, TECHNICAL_DOCUMENTATION.md
├── .env.example
└── railway.toml    Railway deployment config
```

---

## Running tests

```bash
# All tests (requires pytest)
pytest backend/tests/ -v

# Specific suite
pytest backend/tests/test_security.py -v
pytest backend/tests/test_bias_detector.py -v
pytest backend/tests/test_integration_full.py -v

# With coverage
pytest backend/tests/ --cov=backend --cov-report=html
```

Test suites: bias_detector (65), security (40+), cache_and_limits (49), fact_engine (32), recommendations, integration_full, ingestion, processing, models, api.

---

## Deployment (Railway)

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full guide.

**TL;DR:**
1. `railway init` → `railway add --plugin postgresql`
2. Set `SECRET_KEY`, `ALLOWED_ORIGINS`, `INITIAL_ADMIN_EMAIL`, `INITIAL_ADMIN_PASSWORD`
3. `railway up`
4. Visit `https://your-app.railway.app/docs`

---

## Troubleshooting

**`alembic.exc.MissingGreenlet` or similar on startup**
Run `alembic upgrade head` before starting the server.

**`spacy.errors.E050: Can't find model en_core_web_sm`**
```bash
python -m spacy download en_core_web_sm
```

**401 Unauthorized on all endpoints**
Set `AUTH_MODE=disabled` in `.env` for testing, or register a user first:
```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"YourPass1!"}'
```

**Database locked (SQLite)**
SQLite uses WAL mode. Only one writer at a time. Use PostgreSQL for multi-worker (`--workers 4`).

**Port 8000 already in use**
```bash
lsof -i :8000 | awk 'NR>1 {print $2}' | xargs kill -9
```
