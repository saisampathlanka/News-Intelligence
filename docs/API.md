# API Reference — NewsIntel v5

**Base URL:** `http://localhost:8000` (dev) · `https://your-app.onrender.com` (prod)
**Interactive docs:** `GET /docs` (Swagger UI) · `GET /redoc` (ReDoc)

---

## Authentication

All `/insights/*`, `/recommendations/*`, `/facts/*`, and `/admin/*` endpoints require a valid token.

### Token types

| Type | Header | Description |
|------|--------|-------------|
| JWT Bearer | `Authorization: Bearer <token>` | Preferred — from `/auth/login` |
| API Key header | `X-API-Key: nip_<key>` | Server-to-server |
| API Key query | `?api_key=nip_<key>` | Simple integrations |

### Quick auth flow

```bash
# 1. Login
curl -X POST /auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"YourPass1!"}'
# → {"access_token":"eyJ...","refresh_token":"eyJ...","expires_in":1800}

# 2. Call protected endpoint
curl /insights/summary \
  -H "Authorization: Bearer eyJ..."

# 3. Refresh when access_token expires (after 30 min)
curl -X POST /auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token":"eyJ..."}'
```

---

## Auth Endpoints `/auth`

### `POST /auth/register`
Create a new user account. Returns tokens immediately.

**Request body:**
```json
{"email": "user@example.com", "password": "StrongPass1!"}
```

Password rules: 8–128 chars, must contain uppercase + lowercase + digit.

**Response `201`:**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

**Errors:** `409` email already registered · `422` validation error

---

### `POST /auth/login`
Exchange credentials for tokens.

**Request body:**
```json
{"email": "user@example.com", "password": "StrongPass1!"}
```

**Response `200`:** same structure as `/auth/register`

**Errors:** `401` invalid credentials (same message for unknown email or wrong password — prevents user enumeration)

---

### `POST /auth/refresh`
Exchange a refresh token for a new access + refresh token pair.

**Request body:**
```json
{"refresh_token": "eyJ..."}
```

**Response `200`:** new token pair

**Errors:** `401` expired or invalid refresh token

---

### `GET /auth/me` 🔒
Return current user profile. No sensitive fields.

**Response:**
```json
{
  "id": 1,
  "email": "user@example.com",
  "role": "viewer",
  "is_active": true,
  "created_at": "2024-04-20T10:00:00",
  "last_login": "2024-04-22T14:30:00",
  "has_api_key": false
}
```

---

### `POST /auth/api-key` 🔒
Generate or rotate an API key (`nip_` prefix, 40 hex chars).
The raw key is returned **once only** — store it securely.

**Response:**
```json
{
  "api_key": "nip_a1b2c3d4e5f6...",
  "note": "Store this securely — it will not be shown again"
}
```

---

### `POST /auth/revoke` 🔒
Revoke current user's API key. Returns `204 No Content`.

---

### `POST /auth/logout`
Client-side logout hint. Returns `204`. The client must delete stored tokens.

---

### `GET /auth/users` 🔒 Admin
List all users.

### `POST /auth/users/{id}/role` 🔒 Admin
Change a user's role. Body: `?role=viewer` or `?role=admin`

### `POST /auth/users/{id}/deactivate` 🔒 Admin
Deactivate a user account (also revokes their API key).

---

## Articles `/articles`

### `GET /articles`
List articles with filters. Public — no auth required.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `page` | int | 1 | Page number |
| `page_size` | int | 20 | Items per page (max 100) |
| `topic` | string | — | politics · business · technology · health · science · sports · entertainment · world · environment |
| `source` | string | — | Filter by source name (e.g. `Reuters`) |
| `bias_label` | string | — | left · center-left · center · center-right · right |
| `days` | int | — | Articles from last N days |

**Response `200`:**
```json
{
  "articles": [
    {
      "id": 123,
      "url": "https://reuters.com/...",
      "title": "Article headline",
      "description": "Summary...",
      "source_name": "Reuters",
      "published_at": "2024-04-22T10:00:00Z",
      "topic": "politics",
      "bias_label": "center",
      "bias_score": 0.05,
      "bias_confidence": 0.72,
      "sentiment_score": 0.12
    }
  ],
  "total": 500,
  "page": 1,
  "page_size": 20,
  "pages": 25
}
```

---

### `GET /articles/{id}`
Full article detail with NLP analysis. Public.

**Response `200`:**
```json
{
  "id": 123,
  "url": "https://...",
  "title": "...",
  "content": "Full text...",
  "author": "Jane Smith",
  "source_name": "Reuters",
  "published_at": "2024-04-22T10:00:00Z",
  "topic": "politics",
  "bias_score": 0.05,
  "bias_label": "center",
  "bias_confidence": 0.72,
  "bias_signals": [
    "Source 'Reuters' baseline: +0.00",
    "Right keyword: 'free market' (+0.35)"
  ],
  "sentiment_score": -0.3,
  "entities": {
    "people": ["Joe Biden", "Xi Jinping"],
    "organizations": ["NATO", "UN"],
    "places": ["Washington", "Beijing"]
  },
  "keywords": ["diplomacy", "trade", "sanctions"]
}
```

**Errors:** `404` not found

---

### `GET /articles/search/query`
Full-text search (case-insensitive). Public.

| Param | Required | Description |
|-------|----------|-------------|
| `q` | Yes | Search term (min 2 chars) |
| `page`, `page_size` | No | Pagination |

---

## Insights `/insights` 🔒

All insights endpoints require authentication.

### `GET /insights/summary`
Platform overview in a single call. **Cached 60s.**

**Rate limit:** 20/min (expensive tier)

**Response `200`:**
```json
{
  "total_articles": 1250,
  "total_processed": 1180,
  "avg_sentiment": 0.14,
  "last_updated": "2024-04-22T15:30:00Z",
  "topics": [
    {
      "topic": "politics",
      "article_count": 450,
      "avg_bias_score": -0.12,
      "avg_sentiment": 0.08,
      "bias_label": "center-left"
    }
  ],
  "bias_distribution": {
    "left": 85, "center_left": 120,
    "center": 210, "center_right": 95, "right": 40
  }
}
```

---

### `GET /insights/topics`
Topic statistics. **Cached 120s.**

**Response `200`:** array of topic objects (same structure as `topics` above)

---

### `GET /insights/bias-distribution`
Article count by bias label. **Cached 120s.**

**Response `200`:**
```json
{"left": 85, "center_left": 120, "center": 210, "center_right": 95, "right": 40}
```

---

## Trending `/trending`

### `GET /trending`
Topics ranked by velocity, source diversity, and entity mentions. Public.

| Param | Default | Range | Description |
|-------|---------|-------|-------------|
| `hours` | 24 | 1–168 | Lookback window |
| `top_n` | 10 | 1–50 | Results to return |

**Response `200`:**
```json
{
  "trending": [
    {
      "topic": "politics",
      "score": 84.0,
      "article_count": 45,
      "source_count": 8,
      "sources": ["Reuters", "BBC", "Al Jazeera", "..."],
      "top_keywords": ["election", "senate", "vote"],
      "top_entities": ["Biden", "Congress", "Washington"],
      "avg_sentiment": -0.18,
      "sentiment_label": "neutral",
      "bias_variance": 0.65,
      "is_contested": true,
      "sample_headlines": ["...", "..."],
      "trend_velocity": 3.2,
      "window_hours": 24
    }
  ],
  "window_hours": 24,
  "total_topics": 9
}
```

### `GET /trending/entities`
Most-mentioned named entities in recent news.

| Param | Default | Description |
|-------|---------|-------------|
| `hours` | 24 | Lookback window |
| `top_n` | 20 | Max results |

### `GET /trending/corroborated`
Stories covered by N+ independent sources — higher credibility signal.

| Param | Default | Range |
|-------|---------|-------|
| `hours` | 12 | 1–72 |
| `min_sources` | 3 | 2–10 |

---

## Stocks `/stocks`

### `GET /stocks`
Regional market indices. Public. **Cached 60s.**

| Param | Default | Description |
|-------|---------|-------------|
| `region` | `USER_REGION` env | global · west · europe · middle_east · india · southeast_asia · east_asia · africa · latin_america |

**Response `200`:**
```json
{
  "region": "europe",
  "status": "ok",
  "market_mood": "bullish",
  "up_count": 4,
  "down_count": 1,
  "indices": [
    {
      "ticker": "^FTSE",
      "name": "FTSE 100",
      "price": 8245.30,
      "change_pct": 0.42,
      "change_fmt": "+0.42%",
      "direction": "up",
      "currency": "GBP"
    }
  ],
  "updated_at": "2024-04-22T15:45:00Z"
}
```

**Errors:** `400` unknown region

### `GET /stocks/multi`
Multiple regions in one call.

| Param | Default |
|-------|---------|
| `regions` | `global,west,europe` |

### `GET /stocks/regions`
List all regions and their tracked tickers.

---

## Facts `/facts` 🔒

All facts endpoints require authentication. **Rate limit:** 20/min (expensive tier)

### `GET /facts/clusters`
Story clusters — articles about the same event from multiple sources.

| Param | Default | Description |
|-------|---------|-------------|
| `hours` | 24 | Lookback window (max 168) |
| `topic` | — | Filter by topic |
| `min_sources` | 2 | Min sources per cluster |

**Response `200`:**
```json
{
  "clusters": [
    {
      "cluster_id": "cluster_0001",
      "article_ids": [101, 102, 103],
      "article_titles": ["Ukraine talks begin...", "..."],
      "sources": ["Reuters", "BBC World", "Al Jazeera"],
      "topic": "world",
      "common_facts": [
        {"text": "Istanbul", "sources": ["Reuters","BBC","Al Jazeera"], "confidence": 1.0},
        {"text": "Ukraine", "sources": ["Reuters","BBC","Al Jazeera"], "confidence": 1.0}
      ],
      "conflicts": [
        {
          "conflict_type": "numeric",
          "description": "Divergent casualties: 12 vs 45 (3.8x difference)",
          "severity": "high",
          "sources": ["Reuters", "Al Arabiya"]
        }
      ],
      "coverage_start": "2024-04-22T08:00:00",
      "coverage_end": "2024-04-22T14:00:00"
    }
  ],
  "total_clusters": 12,
  "total_articles": 89,
  "window_hours": 24
}
```

### `GET /facts/clusters/{cluster_id}`
Single cluster detail.

### `GET /facts/conflicts`
All conflicts across clusters, sorted by severity.

| Param | Default | Description |
|-------|---------|-------------|
| `hours` | 24 | Lookback |
| `severity` | — | low · medium · high |

### `GET /facts/common-facts`
Cross-source corroborated facts.

| Param | Default | Description |
|-------|---------|-------------|
| `hours` | 24 | Lookback |
| `min_confidence` | 0.55 | Min fraction of sources (0.3–1.0) |
| `topic` | — | Filter by topic |

---

## Recommendations `/recommendations` 🔒

### `GET /recommendations/{article_id}`
Similar articles using weighted 4-signal scoring.

| Param | Default | Range |
|-------|---------|-------|
| `limit` | 5 | 1–20 |

**Response `200`:**
```json
[
  {
    "article": {
      "id": 456, "title": "...", "source_name": "BBC", "topic": "politics"
    },
    "similarity_score": 0.72,
    "relation_type": "entity"
  }
]
```

**Errors:** `404` article not found or not yet processed

### `GET /recommendations/topic/{topic}`
Recent articles for a topic.

| Param | Default | Max |
|-------|---------|-----|
| `limit` | 10 | 50 |

### `GET /recommendations/topics/graph`
Full topic co-occurrence graph.

**Response `200`:**
```json
{
  "nodes": ["business", "politics", "technology", "world"],
  "edges": [
    {"topic_a": "business", "topic_b": "politics", "co_count": 45, "weight": 0.12}
  ],
  "total_articles": 380
}
```

### `GET /recommendations/topics/{topic}/related`
Top related topics by co-occurrence weight.

| Param | Default | Max |
|-------|---------|-----|
| `top_n` | 5 | 20 |

**Response `200`:**
```json
{
  "topic": "politics",
  "related": [
    {"topic": "business", "weight": 0.12, "relation": "co_occurrence"},
    {"topic": "world",    "weight": 0.09, "relation": "co_occurrence"}
  ],
  "count": 2
}
```

---

## Admin `/admin` 🔒 Admin role required

All admin endpoints require `role=admin`.

### `POST /admin/ingest`
Fetch articles from all RSS feeds and optional APIs.

**Response `200`:**
```json
{
  "fetched": 180,
  "saved": 145,
  "deduped_in_memory": 22,
  "deduped_in_db": 13,
  "duration_sec": 8.4
}
```

### `POST /admin/process`
Run NLP processing on unprocessed articles.

| Query param | Default | Max |
|-------------|---------|-----|
| `batch_size` | 100 | 500 |

**Response `200`:**
```json
{"processed": 145, "errors": 2, "duration_sec": 12.1}
```

### `POST /admin/compute-recommendations`
Precompute article similarity graph.

| Query param | Default | Max |
|-------------|---------|-----|
| `batch_size` | 50 | 200 |

### `POST /admin/pipeline`
Full cycle: ingest → process → recommendations → cache invalidation.

**Response `200`:**
```json
{
  "ingestion":                {"fetched": 180, "saved": 145, "...": "..."},
  "processing":               {"processed": 145, "errors": 2, "...": "..."},
  "recommendations_computed": 45,
  "cache_invalidated":        12,
  "status":                   "complete"
}
```

---

## System Endpoints

### `GET /health`
Service health check. Returns `200` when API and DB are both up.

```json
{
  "status": "healthy",
  "db": "connected",
  "version": "1.0.0"
}
```

### `GET /scheduler/status`
Background job schedule and next-run times.

### `GET /cache/stats`
Cache hit/miss statistics.

```json
{
  "size": 24,
  "alive_entries": 18,
  "max_size": 512,
  "hits": 1420,
  "misses": 38,
  "hit_rate": 0.974
}
```

### `POST /cache/invalidate`
Manually invalidate cache entries.

| Query param | Description |
|-------------|-------------|
| `prefix` | Key prefix to clear (empty = clear all) |

---

## Error responses

All errors follow this format:

```json
{"detail": "Human-readable message"}
```

Stack traces are never exposed. Internal errors return:
```json
{"detail": "An internal server error occurred. Please try again later."}
```

| Code | Meaning |
|------|---------|
| 400 | Bad request / invalid parameter |
| 401 | Missing or invalid token |
| 403 | Valid token but insufficient role |
| 404 | Resource not found |
| 409 | Conflict (e.g. email already registered) |
| 422 | Validation error — body shows field-level details |
| 429 | Rate limit exceeded — `Retry-After` header set |
| 500 | Internal server error |
