import logging
import sys
from config.settings import settings

def setup_logging():
    level = logging.DEBUG if settings.DEBUG else logging.INFO
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    
    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("app.log", encoding="utf-8"),
        ]
    )
    # Silence noisy libs
    for lib in ("httpx", "httpcore", "feedparser", "apscheduler"):
        logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("news_intel")
