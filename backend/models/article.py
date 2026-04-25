from sqlalchemy import Column, String, Text, Float, DateTime, Integer, Index, Boolean
from sqlalchemy.sql import func
from backend.core.database import Base
import hashlib


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content_hash = Column(String(64), unique=True, nullable=False, index=True)  # dedup key
    url = Column(String(2048), unique=True, nullable=False)
    title = Column(String(512), nullable=False)
    description = Column(Text)
    content = Column(Text)
    source_name = Column(String(128), nullable=False, index=True)
    source_type = Column(String(32), default="rss")  # rss | newsapi | guardian | gnews
    author = Column(String(256))
    language = Column(String(8), default="en")
    published_at = Column(DateTime, index=True)
    fetched_at = Column(DateTime, server_default=func.now())

    # Processing state
    is_processed = Column(Boolean, default=False, index=True)
    topic = Column(String(128), index=True)
    bias_score = Column(Float)          # -1.0 (left) to +1.0 (right), 0 = neutral
    bias_label = Column(String(32))     # left | center-left | center | center-right | right
    bias_confidence = Column(Float)    # 0.0 to 1.0 — how certain the detection is
    bias_signals_json = Column(Text)   # JSON list of human-readable explanation bullets
    sentiment_score = Column(Float)     # -1.0 to +1.0
    entities_json = Column(Text)        # JSON: {people, orgs, places}
    keywords_json = Column(Text)        # JSON: [kw, ...]

    __table_args__ = (
        Index("ix_articles_topic_published", "topic", "published_at"),
        Index("ix_articles_source_published", "source_name", "published_at"),
    )

    @staticmethod
    def make_hash(url: str, title: str) -> str:
        key = f"{url.strip().lower()}|{title.strip().lower()}"
        return hashlib.sha256(key.encode()).hexdigest()
