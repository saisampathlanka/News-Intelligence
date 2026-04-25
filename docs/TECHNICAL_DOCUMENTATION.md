# Technical Documentation — NewsIntel v5

**Stack:** Python 3.11.9 · FastAPI 0.111 · SQLAlchemy 2.0 · PostgreSQL · spaCy · APScheduler

---

## 1. Project Overview

### Purpose

A news intelligence platform that aggregates articles from 35+ global RSS feeds, applies NLP-based analysis, and surfaces cross-source insights (bias patterns, fact corroboration, topic trends) via a secured REST API and responsive dashboard.

### Core capabilities

| Capability | Implementation |
|------------|---------------|
| Multi-source ingestion | 35+ RSS feeds + NewsAPI/Guardian/GNews APIs |
| Political bias detection | 3-signal: source baseline + keyword weights + framing tone |
| Fact intersection | TF-IDF story clustering + common fact extraction + conflict detection |
| Topic classification | Keyword-density scoring across 9 categories |
| NLP analysis | spaCy NER (people/orgs/places) + sentiment + keyword extraction |
| Article recommendations | Weighted 4-signal similarity + topic co-occurrence graph |
| Market data | 9 regional index bundles via Yahoo Finance (free) |
| Trending detection | Velocity + source diversity + sentiment spike scoring |
| Authentication | JWT + API key, viewer/admin roles |
| Caching | In-memory TTL LRU cache, 6 endpoint groups |
| Rate limiting | 4-tier sliding window per IP |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          CLIENT LAYER                               │
│   Browser Dashboard       Mobile Browser       API Clients / SDKs  │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ HTTPS / REST
┌───────────────────────────────▼─────────────────────────────────────┐
│                    MIDDLEWARE STACK (FastAPI)                        │
│  SecurityHeadersMiddleware   →  X-Frame-Options, nosniff, HSTS      │
│  TieredRateLimitMiddleware   →  10/20/30/60 req/min per IP per tier  │
│  CORSMiddleware              →  ALLOWED_ORIGINS from env            │
└──────────────┬──────────────────────────────────────────────────────┘
               │
┌──────────────▼─────────────────────────────────────────────────────┐
│                       API ROUTER LAYER                              │
│  /auth    /articles   /insights   /trending   /stocks              │
│  /facts   /recommendations   /admin   /health  /cache              │
│                                                                     │
│  Auth guards:  require_auth (viewer+)  require_admin (admin only)  │
└──────┬──────────────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────┐  ┌────────────────────────────────────┐
│       SERVICES LAYER        │  │       SCHEDULER (APScheduler)       │
│                             │  │                                    │
│  Ingestion  Processing      │  │  ingest     every 30min            │
│  BiasDetect FactEngine      │  │  process    every 30min +5min      │
│  NLP        Trending        │  │  recs       every 60min            │
│  StockSvc   Recommendations │  │  stocks     every 15min            │
│                             │  │  cache_purge every 10min           │
└──────┬──────────────────────┘  └────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────────────┐
│                      CACHE LAYER  (in-memory LRU)                   │
│  TTL 30s: /articles list    TTL 60s: /stocks, /insights/summary    │
│  TTL 90s: /trending         TTL 120s: /insights/topics, /clusters  │
└──────┬──────────────────────────────────────────────────────────────┘
       │  cache miss
┌──────▼──────────────────────────────────────────────────────────────┐
│                      DATABASE LAYER                                  │
│  PostgreSQL (production)   SQLite WAL (development)                 │
│  articles · related_articles · topic_stats · users                 │
│  Alembic migrations: 001 → 002 → 003 → 004 → 005                  │
└──────────────────────────────────────────────────────────────────────┘
                                    ▲
┌───────────────────────────────────┤
│         DATA SOURCES              │
│  RSS: 35+ feeds, 9 regions        │
│  NewsAPI (100 req/day free)       │
│  The Guardian (free API key)      │
│  GNews (100 req/day free)         │
└───────────────────────────────────┘
```

---

## 3. Low-Level Design

### Folder structure

```
backend/
├── api/
│   ├── articles.py          GET /articles, GET /articles/{id}, search
│   ├── insights.py          GET /insights/* — cached, require_auth
│   ├── recommendations.py   GET /recommendations/*, topic graph
│   ├── admin.py             POST /admin/* — require_admin
│   ├── facts.py             GET /facts/* — require_auth
│   ├── auth.py              POST/GET /auth/* — user + token management
│   ├── stocks_trending.py   GET /stocks/*, /trending/*
│   └── schemas.py           Pydantic request/response models
├── core/
│   ├── database.py          SQLAlchemy engine, Base, get_db()
│   ├── cache.py             TTL LRU cache, key builders, TTL constants
│   ├── jwt.py               HS256 JWT create/verify (stdlib only)
│   ├── security.py          PBKDF2 password hash/verify, API key gen
│   ├── auth_deps.py         FastAPI Depends: get_current_user, require_admin
│   ├── scheduler.py         APScheduler job definitions
│   └── logging.py           Structured logging setup
├── models/
│   ├── article.py           Article ORM + make_hash()
│   ├── relations.py         RelatedArticle, TopicStats ORM
│   ├── user.py              User ORM + safe_dict()
│   └── schemas.py           RawArticle dataclass (in-memory contract)
├── services/
│   ├── bias_detector.py     3-signal bias engine, BiasResult, aggregate_topic_bias
│   ├── fact_engine.py       TF-IDF clustering, common facts, conflict detection
│   ├── ingestion.py         Fetch → dedup → persist orchestrator
│   ├── nlp_processor.py     spaCy NER, keyword extraction, sentiment
│   ├── processing.py        Batch processor: topic + bias + NLP
│   ├── recommendations.py   4-signal similarity, topic graph, precompute
│   ├── rss_fetcher.py       feedparser + bleach sanitization
│   ├── api_fetchers.py      NewsAPI/Guardian/GNews + tenacity retry
│   ├── stock_service.py     yfinance regional market data
│   ├── topic_classifier.py  Keyword-density topic classification
│   └── trending_service.py  Velocity + diversity trending engine
└── tests/                   12 test modules, 112 tests
```

---

## 4. Security Architecture

### Authentication flow

```
Client                          FastAPI                         Database
  │                                │                               │
  │  POST /auth/login              │                               │
  │  {email, password}  ──────────►│                               │
  │                                │  SELECT * FROM users          │
  │                                │  WHERE email = ?  ────────────►
  │                                │◄─────────────────────────────
  │                                │  PBKDF2 verify(password, hash)│
  │                                │  (timing-safe, 260k iter)     │
  │  {access_token,                │                               │
  │   refresh_token}  ◄────────────│  create_access_token(HS256)   │
  │                                │  create_refresh_token(HS256)  │
  │                                │                               │
  │  GET /insights/summary         │                               │
  │  Authorization: Bearer eyJ... ►│                               │
  │                                │  verify_token(secret)         │
  │                                │  → payload{sub, role, exp}    │
  │                                │  SELECT user WHERE id=sub     │
  │                                │──────────────────────────────►│
  │                                │◄──────────────────────────────│
  │  {summary data}  ◄─────────────│                               │
```

### Token properties

| Property | Value |
|----------|-------|
| Algorithm | HS256 (HMAC-SHA256) |
| Access token lifetime | 30 minutes (configurable) |
| Refresh token lifetime | 7 days (configurable) |
| Claims validated | `exp`, `iat`, `type`, `sub`, `jti` |
| Algorithm confusion (`"alg":"none"`) | Rejected — signature always verified |
| Privilege escalation | Tampered payload → signature mismatch → rejected |
| User enumeration | Login returns identical error for unknown email or wrong password |

### Middleware order (outermost → innermost)

```
SecurityHeadersMiddleware   (runs last on response — adds headers)
TieredRateLimitMiddleware   (429 before request reaches router)
CORSMiddleware              (handles preflight OPTIONS)
FastAPI Request Handler      (auth guards via Depends)
```

### Rate limit tiers

| Tier | Limit | Endpoints |
|------|-------|-----------|
| `admin` | 10/min | `/admin/*` |
| `expensive` | 20/min | `/insights/summary`, `/facts/clusters`, `/facts/conflicts` |
| `stocks` | 30/min | `/stocks/*` |
| `default` | 60/min | Everything else |

Window: sliding 60-second per IP. Response: `429` with `Retry-After` header.

---

## 5. Bias Detection Engine

### 3-signal architecture

```
Signal 1: Source baseline (weight 0.35)
  Lookup source_name in registry of 35 outlets
  Reuters=0.00, The Guardian=-0.30, Arab News=+0.20, ...

Signal 2: Keyword scoring (weight 0.45)
  Per-phrase weights from KEYWORD_SIGNALS list (50+ phrases)
  Average of matched weights → single score
  "radical left" (+0.70) >> "conservative" (+0.35)

Signal 3: Framing amplifier (weight 0.20)
  Attack framing ("threatens", "destroys") → amplifies directional lean
  Balanced framing ("according to data", "both sides") → dampens lean

Composite = (s1 × 0.35) + (s2 × 0.45) + (s3 × 0.20 × directional_lean)
```

### Output: `BiasResult`

```python
@dataclass
class BiasResult:
    score: float              # -1.0 to +1.0
    label: str                # left|center-left|center|center-right|right
    confidence: float         # 0.0 to 1.0 — based on evidence volume
    source_baseline: float    # raw baseline for this source
    keyword_score: float      # keyword signal contribution
    framing_intensity: float  # 0.0 to 1.0
    signals: list[str]        # ["Source 'Reuters' baseline: +0.00", ...]
```

---

## 6. Fact Intersection Engine

### Pipeline

```
1. Fetch recent processed articles (last 24h by default)
2. TF-IDF vectorize (ngram 1-2, sublinear_tf, max_features=10000)
3. Cosine similarity matrix
4. Greedy single-linkage clustering (threshold: 0.18)
5. Per cluster:
   a. Extract noun phrases (proper nouns, quoted phrases, numeric claims)
   b. Count cross-source mentions → common facts at ≥55% threshold
   c. Detect numeric divergence (>50% ratio) → conflicts
   d. Detect outcome verb contradictions → conflicts
6. Return clusters sorted by size
```

### Conflict types

| Type | Detection | Example |
|------|-----------|---------|
| `numeric` | Same context word, values differ >50% | "45 casualties" vs "12 casualties" |
| `outcome` | Antonym verb pairs | "bill approved" vs "bill rejected" |

---

## 7. Recommendation Engine

### 4-signal scoring

```
score = (topic_match × 0.40
       + entity_jaccard × 0.30
       + keyword_jaccard × 0.20
       + perspective_bonus × 0.10) / Σ(active_weights)
```

- **topic_match**: same category = 1.0, different = 0
- **entity_jaccard**: Jaccard(source_entities, candidate_entities)
- **keyword_jaccard**: Jaccard(source_keywords, candidate_keywords)
- **perspective_bonus**: |bias_a − bias_b| ≥ 0.35 = bonus for showing the other side

### Topic co-occurrence graph

- Nodes = topic categories
- Edges = normalized keyword co-mention frequency between topics
- `GET /recommendations/topics/{t}/related` → top-N related topics by edge weight
- Fallback when no edges: return topics by article volume

---

## 8. Caching Layer

### Design

- Pure Python stdlib `OrderedDict` + `threading.Lock` — no Redis required
- LRU eviction when `max_size=512` reached
- Per-entry TTL with `time.monotonic()`
- `cache.invalidate("")` after every pipeline run (admin triggers)
- `purge_expired()` called every 10 minutes by scheduler

### TTL configuration

| Cache key pattern | TTL | Endpoint |
|-------------------|-----|----------|
| `insights:summary` | 60s | `/insights/summary` |
| `insights:topics` | 120s | `/insights/topics` |
| `insights:bias_dist` | 120s | `/insights/bias-distribution` |
| `articles:*` | 30s | `/articles` list |
| `articles/{id}` | 300s | `/articles/{id}` detail |
| `trending:*` | 90s | `/trending` |
| `stocks:*` | 60s | `/stocks` |
| `clusters:*` | 120s | `/facts/clusters` |

---

## 9. Scheduler Jobs

| Job | Frequency | Function |
|-----|-----------|----------|
| `ingest` | Every 30 min | Fetch all RSS + API sources |
| `process` | Every 30 min +5 min offset | NLP process new articles |
| `recommendations` | Every 60 min | Precompute similarity graph |
| `stocks` | Every 15 min | Refresh market data |
| `cache_purge` | Every 10 min | Remove expired cache entries |

**Resilience settings:**
- `coalesce=True` — missed jobs merged into one (no pile-up after downtime)
- `max_instances=1` — no concurrent job overlap
- `misfire_grace_time=60` — jobs >60s late are dropped, not queued

---

## 10. Data Flow

```
[SCHEDULER] triggers _ingest_job() every 30 min
  │
  ├─ rss_fetcher.fetch_rss()  ×35 feeds        → List[RawArticle]
  ├─ api_fetchers.fetch_newsapi()   (optional)  → List[RawArticle]
  ├─ api_fetchers.fetch_guardian()  (optional)  → List[RawArticle]
  └─ api_fetchers.fetch_gnews()     (optional)  → List[RawArticle]
         │
         ▼
  ingestion._save_articles()
    ├─ In-memory dedup: SHA-256 set  →  skip duplicates
    ├─ is_valid(min_words=50)        →  discard short/empty
    └─ DB write with IntegrityError catch per row
         │
         ▼  5 minutes later
  [SCHEDULER] triggers _process_job()
    └─ processing.process_batch(batch_size=100)
         ├─ topic_classifier.classify_topic()  →  9-category keyword density
         ├─ bias_detector.detect_bias_full()   →  3-signal BiasResult
         └─ nlp_processor.process_article_nlp()
              ├─ spaCy NER   →  {people, organizations, places}
              ├─ noun chunks →  top 15 keywords
              └─ sentiment   →  -1.0 to +1.0
         │
         ▼  article.is_processed = True
  [SCHEDULER] triggers _recommendations_job() every 60 min
    └─ recommendations.precompute_relationships()
         ├─ 4-signal scoring per article pair
         └─ store top-5 per article in related_articles
         │
         ▼
  [API REQUEST] GET /insights/summary
    ├─ cache.get("insights:summary")  →  HIT: return cached
    └─ MISS:
         ├─ Q1: SELECT COUNT, COUNT(processed), AVG(sentiment)
         ├─ Q2: SELECT topic, bias_label, COUNT, AVG GROUP BY topic, bias_label
         ├─ Python collapse into InsightsSummary
         └─ cache.set("insights:summary", result, ttl=60)
```

---

## 11. Test Coverage

| Suite | Tests | Coverage |
|-------|-------|---------|
| `test_bias_detector.py` | 65 | BiasResult, all 3 signals, aggregate, backward compat |
| `test_security.py` | 40 | JWT attacks, password timing, API key format, input validation |
| `test_cache_and_limits.py` | 49 | LRU, TTL, invalidation, thread safety, rate tiers |
| `test_fact_engine.py` | 32 | TF-IDF clustering, common facts, numeric/outcome conflicts |
| `test_recommendations.py` | ~40 | 4-signal scoring, topic graph, edge cases |
| `test_integration_full.py` | ~35 | Pipeline end-to-end, API failure, duplicate flood |
| `test_ingestion.py` | 10 | RSS parsing, dedup, validation |
| `test_processing.py` | 21 | Topic classification, bias, NLP |
| `test_models.py` | 12 | ORM schema, hash function |
| `test_api.py` | 9 | API response schemas |
| **Total** | **112+** | All 0 failing |

---

## 12. Scalability Path

| Scale | First bottleneck | Fix |
|-------|-----------------|-----|
| Current (<50 DAU) | — | Free-tier stack sufficient |
| 10K articles/day | 5× `/insights/summary` queries | ✅ Already fixed (2-query + cache) |
| Multiple workers | APScheduler conflict | Move to Celery + Redis broker |
| 100K articles | `ILIKE` search | Add PostgreSQL `tsvector` GIN index |
| 50K DAU | Origin-level DB load | CDN + Cloudflare cache for public endpoints |
| 1M articles | Table scan | Partition `articles` by `published_at` month |
| Better bias | ~55% accuracy | Fine-tune DeBERTa-v3 on AllSides/MBFC dataset |
| Better recs | Jaccard accuracy | Add `pgvector` extension, sentence-transformer embeddings |
