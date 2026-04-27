# 🎯 NewsIntel System Design & Interview Guide

This guide is designed to help you confidently explain, defend, and deep-dive into the architectural decisions behind the Multi-Source News Intelligence Platform. 

---

## 1. 🔍 DEEP EXPLANATIONS

### Component: The Ingestion Pipeline (RSS Fetcher & NLP Processor)
* **How it works:** The system concurrently fetches 35+ RSS XML feeds using `aiohttp`. It extracts the core text, runs it through `spaCy` (en_core_web_sm) to identify named entities (people, places, organizations) and keywords, and stores it in PostgreSQL.
* **Why it was chosen:** RSS provides a standardized, low-latency, and free way to aggregate news without dealing with scraping rate limits or captchas. Async `aiohttp` allows us to fetch 35 feeds in ~2 seconds rather than 35 seconds sequentially.
* **What breaks if removed:** Without this, the platform has no data. If `spaCy` was removed, we would lose all ability to cluster stories, extract metadata, or detect bias based on keyword weighting.

### Component: Bias Detection Engine
* **How it works:** It uses a 3-signal approach. 1) Baseline source bias (e.g., Fox News = Right, CNN = Left). 2) Keyword matching (e.g., checking for loaded terms like "regime" vs "government"). 3) Framing intensity (checking sentiment extremes via `TextBlob`). The final score (-1.0 to 1.0) is a weighted average of these signals.
* **Why it was chosen:** LLM-based bias detection is too slow and expensive for thousands of articles. A heuristic, dictionary-based approach is deterministic, extremely fast, and completely free to run on every single article.
* **What breaks if removed:** The core value proposition of the platform dies. Users wouldn't be able to filter by political leaning, and the "Center Bias %" analytics would stop working.

### Component: Fact Intersection Engine
* **How it works:** It uses TF-IDF (Term Frequency-Inverse Document Frequency) and cosine similarity to group articles from different publishers about the exact same event. It then extracts overlapping named entities to construct the "common facts."
* **Why it was chosen:** TF-IDF is highly efficient and runs purely on CPU memory without needing a dedicated vector database like Pinecone or an embedding model API. 
* **What breaks if removed:** We would just be a generic news feed with thousands of duplicates. We wouldn't be able to highlight "contested facts" across the political spectrum.

### Component: Catch-All Exception Handler & UI
* **How it works:** A global `ExceptionMiddleware` intercepts 404, 401, 403, and 405 HTTP status codes. If the request `Accept` header indicates a browser (`text/html`), it returns a branded, animated HTML response instead of raw JSON.
* **Why it was chosen:** It provides a premium, seamless user experience. If an admin clicks a broken link or an unauthorized user tries to view insights, they get a polished UI rather than an ugly raw JSON dump.
* **What breaks if removed:** The API functions normally, but human users navigating the browser hit dead ends with raw `{ "detail": "Not Found" }` text, making the platform feel like a cheap backend rather than a premium product.

---

## 2. 🎯 STRONG TECHNICAL ANSWERS

**How do you detect bias?**
"We use a deterministic 3-signal heuristic model. First, we establish a baseline leaning based on the publisher's known historical bias. Then, we extract the text and calculate a lexical weight using a dictionary of politically loaded terms. Finally, we measure framing intensity using polarity scores. We combine these into a normalized score between -1 and 1. It's not perfect, but it's incredibly fast and scalable."

**How do you define “truth”?**
"We don't. We define 'consensus.' The system clusters articles about the same event from Left, Center, and Right sources. It extracts the Named Entities (people, places) that appear across all of them. Whatever overlaps across the spectrum is presented as the 'common fact.' Everything else is presented as publisher framing."

**How do you handle duplicate articles?**
"During ingestion, we do a quick check on the exact URL and the exact title. For semantic duplicates—where two outlets cover the same story—we use TF-IDF vectorization and cosine similarity to group them into 'Clusters'. We don't delete them; we link them together."

**How does request flow work?**
"A user request hits the FastAPI router. It first goes through a rate-limiting dependency backed by an in-memory sliding window cache. If authenticated, the JWT is validated. The route handler then queries PostgreSQL via SQLAlchemy, passes the data through Pydantic models for serialization, and returns the JSON response."

**Why FastAPI?**
"Speed and Developer Experience. FastAPI uses Starlette underneath, giving us native async/await for our heavy I/O tasks like RSS fetching. Plus, it automatically generates our OpenAPI documentation and uses Pydantic for strict schema validation, preventing bad data from hitting the database."

**Why PostgreSQL?**
"Relational integrity and JSONB support. News data is highly relational (Articles belong to Sources, Users have Bookmarks), but we also have unstructured metadata like extracted keywords and entities. Postgres gives us strict ACID compliance while letting us query the unstructured `JSONB` entity data natively."

**How does async help?**
"Our ingestion pipeline relies on network calls to 35 different external servers. In a synchronous app, the thread would block and wait for Server A to respond before calling Server B. With `asyncio`, we fire off all 35 requests concurrently. The CPU is freed up to do other tasks while waiting for the network packets to return."

---

## 3. ⚠️ CHALLENGE QUESTIONS

**“What happens under 10k requests/sec?”**
"The current architecture would buckle. Right now, we use an in-memory cache and a single Postgres instance. To handle 10k RPS, I would move the cache to a dedicated Redis cluster, place a CDN like Cloudflare in front to serve the static frontend and heavily cache the `GET /articles` endpoint, and add read-replicas to the Postgres database."

**“How do you prevent bias in bias detection?”**
"You can't eliminate it entirely because the dictionaries themselves are curated by humans. We mitigate this by keeping the source code and dictionaries transparent. Furthermore, the baseline scores rely on established media watchdogs like Ad Fontes Media. We treat bias as a continuous spectrum, not a binary label."

**“What if sources contradict each other?”**
"The Fact Engine detects this. If Source A says a bill passed and Source B says it failed, the TF-IDF clustering groups them, but the entity extraction will flag a low overlap ratio. We expose a `/facts/conflicts` endpoint specifically to highlight these highly contested stories to the user."

---

## 4. 🧠 FAILURE SCENARIOS

* **API Failure Handling:** We use a global exception handler. If a route fails or throws a 500, we catch it, log the stack trace internally, and return a sanitized, user-friendly HTML or JSON response (depending on the client's `Accept` header) to prevent leaking stack traces.
* **Data Inconsistency:** We use SQLAlchemy transactions. During ingestion, if we insert an article but the NLP processing fails halfway through, the database transaction rolls back. We don't end up with half-processed, corrupted rows.
* **High Traffic Spikes:** The application implements a 4-tier sliding window rate limiter per IP address. If a single IP aggressively polls the API, they will hit a 429 Too Many Requests status code before they can exhaust connection pools to the database.

---

## 5. 🗣️ SPEAKING MODE (Elevator Pitches)

* **The Project:** "I built a News Intelligence Platform that ingests live data from 35 global sources, uses NLP to detect political bias and sentiment, and surfaces the consensus facts across the political spectrum."
* **The Architecture:** "It's built on a decoupled architecture. The backend is an async FastAPI server backed by PostgreSQL, running a scheduled NLP ingestion pipeline. The frontend is a modular, vanilla JavaScript dashboard that dynamically fetches data and user-localized stock metrics."
* **The Hardest Part:** "The hardest part was stabilizing the deployment. I had to orchestrate the backend and frontend separately on Railway, ensuring the database migrations ran synchronously during the startup lifespan before the web server began accepting connections, and implementing global catch-all exception routing for the frontend."
