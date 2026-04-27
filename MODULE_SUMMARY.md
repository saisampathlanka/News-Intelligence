# NewsIntel — Module Summary v5

All modules are implemented and passing tests (112/112).

---

## Core Modules

### Module 1 — Bias Detection (upgraded)
**Files:** `backend/services/bias_detector.py`

3-signal engine replacing the original keyword-count heuristic:

| Signal | Weight | Implementation |
|--------|--------|----------------|
| Source baseline | 0.35 | 35-outlet registry compiled from AllSides/MBFC |
| Keyword scoring | 0.45 | Per-phrase weights (50+ phrases, e.g. "radical left" = +0.70) |
| Framing amplifier | 0.20 | Attack language amplifies lean; balanced language dampens |

Output: `BiasResult` dataclass with `score`, `label`, `confidence`, `signals[]` (full explainability).

**Tests:** 65 tests in `test_bias_detector.py`

---

### Module 2 — Fact Intersection Engine (new)
**Files:** `backend/services/fact_engine.py`, `backend/api/facts.py`

TF-IDF story clustering + fact/conflict extraction:

1. Vectorize article text (ngram 1–2, sublinear_tf)
2. Cosine similarity → greedy cluster (threshold 0.18)
3. Extract common facts (≥55% cross-source mention)
4. Detect conflicts: numeric divergence >50% OR outcome verb contradictions

**Endpoints:** `GET /facts/clusters`, `/facts/conflicts`, `/facts/common-facts`

**Tests:** 32 tests in `test_fact_engine.py`

---

### Module 3 — Database Optimization
**Files:** `alembic/versions/004_query_indexes.py`

4 composite indexes targeting actual query patterns:

| Index | Columns | Query it serves |
|-------|---------|-----------------|
| `ix_articles_processed_topic` | `(is_processed, topic)` | `/articles?topic=` + batch processor |
| `ix_articles_processed_fetched` | `(is_processed, fetched_at)` | Processing queue order |
| `ix_articles_bias_label` | `bias_label` | Bias distribution GROUP BY |
| `ix_related_similarity` | `similarity_score` | Recommendation ORDER BY |

`/insights/summary` reduced from 5 queries → 2 (combined aggregate + single GROUP BY).

---

### Module 4 — Caching Layer (new)
**Files:** `backend/core/cache.py`

In-memory TTL LRU cache (pure stdlib, no Redis):
- Thread-safe `OrderedDict` + `threading.Lock`
- Per-entry TTL, LRU eviction at `max_size=512`
- `cache.invalidate("")` called after every pipeline run
- Scheduler purges expired entries every 10 minutes

**Cached endpoints:** `/articles`, `/insights/*`, `/trending`, `/stocks`, `/facts/clusters`

**Tests:** 49 tests in `test_cache_and_limits.py`

---

### Module 5 — Rate Limiting
**Files:** `backend/main.py` (`TieredRateLimitMiddleware`)

Sliding 60-second window per IP with 4 tiers:

| Tier | Limit | Endpoints |
|------|-------|-----------|
| admin | 10/min | `/admin/*` |
| expensive | 20/min | `/insights/summary`, `/facts/clusters`, `/facts/conflicts` |
| stocks | 30/min | `/stocks/*` |
| default | 60/min | Everything else |

Returns `429` with `Retry-After` header.

---

### Module 6 — Testing
**Files:** `backend/tests/` (12 modules, 112 tests)

| File | Tests | Coverage |
|------|-------|---------|
| `test_bias_detector.py` | 65 | All 3 signals, aggregate, backward compat |
| `test_security.py` | 40+ | JWT attacks, password timing, input validation |
| `test_cache_and_limits.py` | 49 | LRU, TTL, invalidation, thread safety |
| `test_fact_engine.py` | 32 | Clustering, facts, conflict detection |
| `test_recommendations.py` | ~40 | 4-signal scoring, topic graph |
| `test_integration_full.py` | ~35 | Pipeline, API failure, duplicate flood |
| `test_ingestion.py` | 10 | RSS parsing, dedup |
| `test_processing.py` | 21 | Topic, bias, NLP |
| `test_models.py` | 12 | ORM schema, hash |
| `test_api.py` | 9 | Response schemas |

---

### Module 7 — Recommendation Engine (upgraded)
**Files:** `backend/services/recommendations.py`, `backend/api/recommendations.py`

**Weighted 4-signal scoring** (replaces simple averaging):

```
score = (topic_match × 0.40
       + entity_jaccard × 0.30
       + keyword_jaccard × 0.20
       + perspective_bonus × 0.10) ÷ Σ(active_weights)
```

**Topic co-occurrence graph:**
- Nodes = topic categories
- Edges = normalized shared-keyword frequency between topic groups
- `GET /recommendations/topics/{t}/related` → top-N related topics

---

### Module 8 — Security (new)
**Files:** `backend/core/jwt.py`, `backend/core/security.py`, `backend/core/auth_deps.py`, `backend/models/user.py`, `backend/api/auth.py`

Full authentication system:

| Component | Implementation |
|-----------|---------------|
| JWT | HS256 (stdlib: `hmac` + `hashlib` + `base64`) — no `jose`/`pyjwt` |
| Passwords | PBKDF2-HMAC-SHA256, 260,000 iterations, 32-byte salt — no `bcrypt` |
| API keys | `nip_` + 40-char hex, stored in `users.api_key` |
| Token extraction | Bearer header → X-API-Key header → `?api_key=` query param |
| Guards | `require_auth` (viewer+), `require_admin` (admin only) |
| Security headers | X-Frame-Options=DENY, nosniff, HSTS, removes Server header |
| Error handling | Global handler — no stack traces, generic 500 message |

**Protected endpoints:**
- `/insights/*`, `/recommendations/*`, `/facts/*` — `require_auth`
- `/admin/*` — `require_admin`

**Auth endpoints:** `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/me`, `/auth/api-key`, `/auth/revoke`, `/auth/logout`, `/auth/users` (admin), `/auth/users/{id}/role` (admin)

**DB:** `users` table via migration `005_users`

**Tests:** 40 tests in `test_security.py`

---

## Migration Chain

```
001_initial          articles table, core indexes
002_relations        related_articles, topic_stats
003_bias_confidence  bias_confidence, bias_signals_json columns
004_query_indexes    4 composite query indexes
005_users            users table and auth indexes
```

Apply all: `alembic upgrade head`

---

### Module 9 — Frontend Architecture (refactored)
**Files:** `frontend/index.html`, `frontend/style.css`, `frontend/app.js`

Fully decoupled presentation, logic, and style:
- **`index.html`**: Pure structural markup and layout elements.
- **`style.css`**: CSS grid, mobile-first responsive design, animated UI components (skeletons, toast notifications).
- **`app.js`**: Vanilla JS asynchronous logic.
   - Dynamic role-based rendering (Refresh pipeline only visible to admins).
   - Global auth/session token handling across components.
   - Smart geolocation mapping: Detects user coordinates, maps to geographic boundaries, and requests localized financial data.
   - Auto-refresh synchronization after Auth events.

---

### Module 10 — Deployment & Resilience (new)
**Files:** `backend/main.py` (`ExceptionMiddleware`, `/health`), `railway.toml`, `frontend/railway.toml`

Dual-Service CI/CD on Railway:
- **API Service**: Runs Python 3.11 with FastAPI. Bootstraps PostgreSQL migrations via Alembic `startCommand` ensuring database schema matches API state before traffic is routed. Global Exception Middleware traps any internal 4xx/5xx failures and routes them to a branded Custom HTML error page.
- **Frontend Service**: Vanilla JS static host linked directly to the API via environment variables.

---

## File Count

| Category | Count |
|----------|-------|
| Python source files | 57 |
| Test modules | 12 |
| Alembic migrations | 5 |
| Documentation files | 5 |
| Deployment configs | 1 |
| Syntax errors | 0 |
| Failing tests | 0 |
