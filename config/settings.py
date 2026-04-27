from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional


# ── Regional RSS Feed Registry ────────────────────────────────────────────────
# Format: (source_name, url, region)
RSS_FEED_REGISTRY = {

    "global": [
        ("Reuters Top News",   "https://feeds.reuters.com/reuters/topNews",           "global"),
        ("Reuters Business",   "https://feeds.reuters.com/reuters/businessNews",      "global"),
        ("Reuters World",      "https://feeds.reuters.com/reuters/worldNews",         "global"),
        ("AP News",            "https://rsshub.app/apnews/topics/apf-topnews",        "global"),
        ("BBC World",          "http://feeds.bbci.co.uk/news/world/rss.xml",          "global"),
        ("BBC Business",       "http://feeds.bbci.co.uk/news/business/rss.xml",       "global"),
    ],

    "west": [
        ("The Guardian World", "https://www.theguardian.com/world/rss",               "west"),
        ("The Guardian US",    "https://www.theguardian.com/us-news/rss",             "west"),
        ("NPR News",           "https://feeds.npr.org/1001/rss.xml",                  "west"),
    ],

    "europe": [
        ("Deutsche Welle",     "https://rss.dw.com/rdf/rss-en-all",                  "europe"),
        ("DW Europe",          "https://rss.dw.com/xml/rss-en-eu",                   "europe"),
        ("France 24",          "https://www.france24.com/en/rss",                     "europe"),
        ("Euronews",           "https://www.euronews.com/rss",                        "europe"),
        ("POLITICO Europe",    "https://www.politico.eu/feed/",                       "europe"),
        ("EUobserver",         "https://euobserver.com/rss.xml",                      "europe"),
    ],

    "middle_east": [
        ("Al Jazeera",         "https://www.aljazeera.com/xml/rss/all.xml",           "middle_east"),
        ("Al Arabiya",         "https://english.alarabiya.net/tools/rss",             "middle_east"),
        ("Middle East Eye",    "https://www.middleeasteye.net/rss",                   "middle_east"),
        ("Jerusalem Post",     "https://www.jpost.com/Rss/RssFeedsHeadlines.aspx",   "middle_east"),
        ("Arab News",          "https://www.arabnews.com/rss.xml",                    "middle_east"),
    ],

    "india": [
        ("The Hindu",          "https://www.thehindu.com/feeder/default.rss",         "india"),
        ("NDTV",               "https://feeds.feedburner.com/ndtvnews-top-stories",   "india"),
        ("Times of India",     "https://timesofindia.indiatimes.com/rssfeedstopstories.cms", "india"),
        ("The Print",          "https://theprint.in/feed/",                           "india"),
        ("The Wire",           "https://thewire.in/feed",                             "india"),
    ],

    "southeast_asia": [
        ("Channel News Asia",  "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml", "southeast_asia"),
        ("Straits Times",      "https://www.straitstimes.com/news/asia/rss.xml",      "southeast_asia"),
        ("Asia Times",         "https://asiatimes.com/feed/",                         "southeast_asia"),
        ("Nikkei Asia",        "https://asia.nikkei.com/rss/feed/site",               "southeast_asia"),
    ],

    "east_asia": [
        ("SCMP",               "https://www.scmp.com/rss/91/feed",                    "east_asia"),
        ("SCMP China",         "https://www.scmp.com/rss/4/feed",                     "east_asia"),
        ("The Diplomat",       "https://thediplomat.com/feed/",                       "east_asia"),
        ("Japan Times",        "https://www.japantimes.co.jp/feed/",                  "east_asia"),
        ("Korea Herald",       "https://www.koreaherald.com/common/rss_xml.php?ct=102", "east_asia"),
    ],

    "africa": [
        ("AllAfrica",          "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf", "africa"),
        ("Daily Maverick",     "https://www.dailymaverick.co.za/feed/",               "africa"),
        ("African Arguments",  "https://africanarguments.org/feed/",                  "africa"),
    ],

    "latin_america": [
        ("Buenos Aires Herald","https://buenosairesherald.com/feed",                  "latin_america"),
        ("Rio Times",          "https://www.riotimesonline.com/feed/",                "latin_america"),
    ],
}

DEFAULT_RSS_FEEDS = [
    url for region in ["global", "west", "europe", "middle_east"]
    for _, url, _ in RSS_FEED_REGISTRY.get(region, [])
]


class Settings(BaseSettings):
    APP_NAME: str = "News Intelligence Platform"
    ENV: str = "development"
    DEBUG: bool = True
    # ── Security ──────────────────────────────────────────────────────────────
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    # MUST be set via environment variable in production — never commit this
    SECRET_KEY: str = "CHANGE_THIS_IN_PRODUCTION_USE_SECRETS_TOKEN_HEX_32"

    # JWT token lifetimes (override in .env if needed)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS — comma-separated list of allowed frontend origins
    # Example: "https://your-frontend.up.railway.app,https://localhost:3000"
    ALLOWED_ORIGINS: str = "https://news-intel-frontend-production-e010.up.railway.app,http://localhost:3000,http://localhost:8080"

    # First-run admin account (created on startup if no users exist)
    # Override in .env — remove from env after first login
    INITIAL_ADMIN_EMAIL:    Optional[str] = None
    INITIAL_ADMIN_PASSWORD: Optional[str] = None

    # Authentication mode
    # "required" — all /insights, /recommendations, /facts endpoints need auth
    # "optional" — endpoints work without auth but provide extra features when authed
    # "disabled" — no auth enforced (development only)
    AUTH_MODE: str = "required"

    DATABASE_URL: str = "sqlite:///./news_intel.db"

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def fix_database_url(cls, v: str) -> str:
        # If DATABASE_URL is unset or blank (e.g. Railway reference variable didn't
        # resolve because the service isn't linked yet), fall back to SQLite so the
        # container at least starts with a clear error rather than a cryptic crash.
        if not v or not v.strip():
            import warnings
            warnings.warn(
                "DATABASE_URL is empty — falling back to SQLite. "
                "In Railway: set DATABASE_URL = ${{<db-service-name>.DATABASE_URL}} "
                "and make sure the DB service is linked to this service.",
                RuntimeWarning,
                stacklevel=2,
            )
            return "sqlite:///./news_intel.db"

        # Railway PostgreSQL URLs use the 'postgres://' scheme.
        # SQLAlchemy 2.x only accepts 'postgresql://' — rewrite it here.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://"):]

        return v

    NEWSAPI_KEY: Optional[str] = None
    GUARDIAN_API_KEY: Optional[str] = None
    GNEWS_API_KEY: Optional[str] = None

    RSS_FEEDS: list = DEFAULT_RSS_FEEDS
    ENABLED_REGIONS: list = ["global", "west", "europe", "middle_east"]
    USER_REGION: str = "global"

    FETCH_INTERVAL_MINUTES: int = 30
    MAX_ARTICLES_PER_SOURCE: int = 30
    STOCK_UPDATE_INTERVAL_MINUTES: int = 15
    # ── Rate Limiting — per-endpoint budgets (requests/minute per IP) ───────────
    # Used by TieredRateLimitMiddleware in backend/main.py
    RATE_LIMIT_DEFAULT:   int = 60   # articles, trending, health, etc.
    RATE_LIMIT_EXPENSIVE: int = 20   # /insights/summary, /facts/clusters, /facts/conflicts
    RATE_LIMIT_ADMIN:     int = 10   # /admin/* pipeline triggers
    RATE_LIMIT_STOCKS:    int = 30   # /stocks/* (external yfinance calls)

    # ── Cache TTLs (seconds) ──────────────────────────────────────────────────
    # Used by endpoints in backend/api/ after cache miss
    CACHE_TTL_SUMMARY:  int = 60    # /insights/summary
    CACHE_TTL_TOPICS:   int = 120   # /insights/topics, /insights/bias-distribution
    CACHE_TTL_ARTICLES: int = 30    # /articles list
    CACHE_TTL_TRENDING: int = 90    # /trending
    CACHE_TTL_STOCKS:   int = 60    # /stocks

    SPACY_MODEL: str = "en_core_web_sm"
    MIN_ARTICLE_WORDS: int = 50

    TRENDING_WINDOW_HOURS: int = 24
    TRENDING_TOP_N: int = 10

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

