"""
RelatedArticles: stores similarity/relationships between articles.
Used for 'related articles' recommendations.
"""
from sqlalchemy import Column, Integer, Float, String, ForeignKey, Index, DateTime
from sqlalchemy.sql import func
from backend.core.database import Base


class RelatedArticle(Base):
    __tablename__ = "related_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    related_id = Column(Integer, ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, index=True)
    
    similarity_score = Column(Float, nullable=False)  # 0.0 to 1.0
    relation_type = Column(String(32), nullable=False)  # topic | entity | keyword | bias
    
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_related_article_score", "article_id", "similarity_score"),
        Index("ix_related_unique", "article_id", "related_id", unique=True),
    )


# Aggregated stats table for analytics
class TopicStats(Base):
    __tablename__ = "topic_stats"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    topic = Column(String(128), nullable=False, unique=True, index=True)
    article_count = Column(Integer, default=0)
    avg_bias_score = Column(Float)
    avg_sentiment = Column(Float)
    last_updated = Column(DateTime, server_default=func.now(), onupdate=func.now())
