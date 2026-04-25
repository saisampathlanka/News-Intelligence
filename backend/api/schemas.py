"""
API response schemas using Pydantic.
Separates DB models from API contracts.
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime


class ArticleBase(BaseModel):
    id: int
    url: str
    title: str
    description: Optional[str] = None
    source_name: str
    published_at: Optional[datetime] = None
    topic: Optional[str] = None
    bias_label: Optional[str] = None
    bias_score: Optional[float] = None
    sentiment_score: Optional[float] = None

    class Config:
        from_attributes = True


class ArticleDetail(ArticleBase):
    """Extended article with full content and metadata."""
    content: Optional[str] = None
    author: Optional[str] = None
    language: str = "en"
    entities: Optional[Dict[str, List[str]]] = None
    keywords: Optional[List[str]] = None
    bias_signals: Optional[List[str]] = None
    fetched_at: Optional[datetime] = None


class ArticleList(BaseModel):
    """Paginated article list."""
    articles: List[ArticleBase]
    total: int
    page: int
    page_size: int
    pages: int


class TopicSummary(BaseModel):
    """Topic statistics."""
    topic: str
    article_count: int
    avg_bias_score: Optional[float] = None
    avg_sentiment: Optional[float] = None
    bias_label: Optional[str] = None  # derived from avg_bias_score


class RelatedArticleResponse(BaseModel):
    """Related article with similarity score."""
    article: ArticleBase
    similarity_score: float
    relation_type: str


class BiasDistribution(BaseModel):
    """Bias breakdown by label."""
    left: int = 0
    center_left: int = 0
    center: int = 0
    center_right: int = 0
    right: int = 0


class InsightsSummary(BaseModel):
    """Aggregate insights across all articles."""
    total_articles: int
    total_processed: int
    topics: List[TopicSummary]
    bias_distribution: BiasDistribution
    avg_sentiment: float
    last_updated: datetime


class IngestionStats(BaseModel):
    """Ingestion job results."""
    fetched: int
    saved: int
    deduped_in_memory: int
    deduped_in_db: int
    duration_sec: float


class ProcessingStats(BaseModel):
    """Processing job results."""
    processed: int
    errors: int
    duration_sec: float
