# Database Schema — NewsIntel v5

**Engines supported:** PostgreSQL 14+ (production) · SQLite 3 with WAL mode (development)

---

## Migration chain

Apply all migrations in order:

```bash
alembic upgrade head
```

| Revision | File | Changes |
|----------|------|---------|
| `001_initial` | `001_initial.py` | `articles` table, core indexes |
| `002_relations` | `002_relations.py` | `related_articles`, `topic_stats` |
| `003_bias_confidence` | `003_bias_confidence.py` | `bias_confidence`, `bias_signals_json` columns |
| `004_query_indexes` | `004_query_indexes.py` | 4 composite query-pattern indexes |
| `005_users` | `005_users.py` | `users` table, auth indexes |

---

## Tables

### `articles`

Primary content store with all NLP metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | Internal ID |
| `content_hash` | VARCHAR(64) | UNIQUE, NOT NULL, indexed | SHA-256(url+title) — dedup key |
| `url` | VARCHAR(2048) | UNIQUE, NOT NULL | Article URL |
| `title` | VARCHAR(512) | NOT NULL | Headline |
| `description` | TEXT | — | Summary / excerpt |
| `content` | TEXT | — | Full text (max 5000 chars after bleach strip) |
| `source_name` | VARCHAR(128) | NOT NULL, indexed | Publisher name |
| `source_type` | VARCHAR(32) | default `rss` | rss · newsapi · guardian · gnews |
| `author` | VARCHAR(256) | — | Byline |
| `language` | VARCHAR(8) | default `en` | ISO language code |
| `published_at` | DATETIME | indexed | Original publication time (UTC) |
| `fetched_at` | DATETIME | server_default=now() | Ingestion timestamp |
| `is_processed` | BOOLEAN | default false, indexed | Processing gate |
| `topic` | VARCHAR(128) | indexed | Classified category |
| `bias_score` | FLOAT | — | −1.0 (left) to +1.0 (right) |
| `bias_label` | VARCHAR(32) | — | left · center-left · center · center-right · right |
| `bias_confidence` | FLOAT | — | 0.0–1.0 detection confidence |
| `bias_signals_json` | TEXT | — | JSON list of explanation strings |
| `sentiment_score` | FLOAT | — | −1.0 to +1.0 |
| `entities_json` | TEXT | — | `{"people":[],"organizations":[],"places":[]}` |
| `keywords_json` | TEXT | — | `["keyword", ...]` |

**Indexes on `articles`:**

| Index name | Columns | Purpose |
|------------|---------|---------|
| `ix_articles_content_hash` | `content_hash` | Deduplication lookup |
| `ix_articles_source_name` | `source_name` | Filter by source |
| `ix_articles_published_at` | `published_at` | Date range queries |
| `ix_articles_is_processed` | `is_processed` | Batch processing filter |
| `ix_articles_topic` | `topic` | Topic filter |
| `ix_articles_topic_published` | `(topic, published_at)` | Recent articles by category |
| `ix_articles_source_published` | `(source_name, published_at)` | Recent by source |
| `ix_articles_processed_topic` | `(is_processed, topic)` | Batch processor + topic filter |
| `ix_articles_processed_fetched` | `(is_processed, fetched_at)` | Batch queue ordering |
| `ix_articles_bias_label` | `bias_label` | Bias distribution grouping |

---

### `related_articles`

Pre-computed article similarity graph. Populated by scheduler every hour.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK | — |
| `article_id` | INTEGER | FK → articles.id CASCADE | Source article |
| `related_id` | INTEGER | FK → articles.id CASCADE | Related article |
| `similarity_score` | FLOAT | NOT NULL | 0.0–1.0 composite similarity |
| `relation_type` | VARCHAR(32) | NOT NULL | topic · entity · keyword · perspective |
| `created_at` | DATETIME | server_default=now() | When computed |

**Constraints:** `UNIQUE(article_id, related_id)` — one relationship per pair

**Indexes:**

| Index | Column | Purpose |
|-------|--------|---------|
| `ix_related_articles_article_id` | `article_id` | Find all relations for an article |
| `ix_related_articles_related_id` | `related_id` | Reverse lookup |
| `ix_related_similarity` | `similarity_score` | ORDER BY score DESC |

---

### `topic_stats`

Aggregated analytics cache. Updated hourly by scheduler.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | — |
| `topic` | VARCHAR(128) UNIQUE, indexed | Category name |
| `article_count` | INTEGER | Total articles |
| `avg_bias_score` | FLOAT | Mean bias across topic |
| `avg_sentiment` | FLOAT | Mean sentiment |
| `last_updated` | DATETIME | Last computation |

---

### `users`

Authentication accounts.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, autoincrement | Internal ID |
| `email` | VARCHAR(256) | UNIQUE, NOT NULL, indexed | Login email |
| `hashed_password` | VARCHAR(256) | NOT NULL | PBKDF2-HMAC-SHA256 (260k iterations) |
| `role` | VARCHAR(32) | NOT NULL, default `viewer` | viewer · admin |
| `api_key` | VARCHAR(64) | UNIQUE, nullable, indexed | `nip_` prefixed key |
| `is_active` | BOOLEAN | NOT NULL, default true | Soft delete / suspension |
| `created_at` | DATETIME | server_default=now() | Account creation |
| `last_login` | DATETIME | nullable | Last successful auth |

**Indexes:**

| Index | Columns | Purpose |
|-------|---------|---------|
| `ix_users_email` | `email` | Login lookup (unique) |
| `ix_users_api_key` | `api_key` | API key auth lookup (unique) |
| `ix_users_email_active` | `(email, is_active)` | Active user login |

**Security notes:**
- `hashed_password` is never returned by any API endpoint
- `api_key` value is returned once (at generation) and never again — `has_api_key` boolean is returned instead
- `safe_dict()` method on the model explicitly excludes both fields

---

## Entity Relationships

```
articles ──────────────────────┐
    │ (article_id FK)           │ (related_id FK)
    └──► related_articles ◄─────┘
              ↑
    Many-to-many self-join on articles
    One row per (article_id, related_id) pair

users ── standalone auth table
         No FK to articles (articles are public metadata)
```

---

## Query patterns

### Batch processing (uses `ix_articles_processed_fetched`)
```sql
SELECT * FROM articles
WHERE is_processed = FALSE
ORDER BY fetched_at DESC
LIMIT 100;
```

### Insights summary — Query 1 (global aggregates)
```sql
SELECT
  COUNT(id)                                         AS total,
  COUNT(id) FILTER (WHERE is_processed = TRUE)      AS processed,
  AVG(sentiment_score) FILTER (WHERE sentiment_score IS NOT NULL) AS avg_sent
FROM articles;
```

### Insights summary — Query 2 (topic + bias breakdown, single pass)
```sql
SELECT topic, bias_label, COUNT(id) AS cnt,
       AVG(bias_score), AVG(sentiment_score)
FROM articles
WHERE is_processed = TRUE AND topic IS NOT NULL
GROUP BY topic, bias_label
ORDER BY cnt DESC;
```

### Recommendation lookup (uses `ix_related_similarity`)
```sql
SELECT ra.*, a.*
FROM related_articles ra
JOIN articles a ON ra.related_id = a.id
WHERE ra.article_id = :id
ORDER BY ra.similarity_score DESC
LIMIT 5;
```

---

## Deduplication

Articles are deduplicated by a SHA-256 content hash:

```python
hash = SHA-256(url.strip().lower() + "|" + title.strip().lower())
```

Two-layer guard:
1. In-memory set dedup within each ingestion cycle (O(n) pass before any DB calls)
2. `UNIQUE` constraint on `content_hash` + `url` catches cross-cycle duplicates via `IntegrityError`

---

## Development vs Production

| Concern | SQLite (dev) | PostgreSQL (prod) |
|---------|-------------|-------------------|
| Setup | Zero — file-based | Requires running server |
| Multi-writer | WAL mode — single writer | Row-level locking |
| Multi-worker | ❌ Breaks under `--workers > 1` | ✅ Required for multiple workers |
| Full-text search | LIKE (slow at scale) | `tsvector` + GIN index (fast) |
| Storage | Local `.db` file | Remote, managed backups |
| Switch | Change `DATABASE_URL` only | Same ORM code |
