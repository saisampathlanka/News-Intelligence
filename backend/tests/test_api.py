"""
API endpoint tests.
Run: pytest backend/tests/test_api.py -v
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.article import Article


@pytest.fixture
def db():
    """In-memory test database."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_articles(db):
    """Create sample articles for testing."""
    articles = [
        Article(
            content_hash=f"hash_{i}",
            url=f"https://test.com/{i}",
            title=f"Article {i} about politics and economy",
            description=f"Description {i}",
            content=f"Content {i}",
            source_name="Test Source",
            source_type="rss",
            published_at=datetime(2024, 1, i+1, tzinfo=timezone.utc),
            is_processed=True,
            topic="politics" if i % 2 == 0 else "business",
            bias_score=0.3 if i % 2 == 0 else -0.3,
            bias_label="center-right" if i % 2 == 0 else "center-left",
            sentiment_score=0.5,
            entities_json='{"people": ["Person A"], "organizations": ["Org B"], "places": ["City C"]}',
            keywords_json='["keyword1", "keyword2"]',
        )
        for i in range(10)
    ]
    db.add_all(articles)
    db.commit()
    return articles


# ── Schema Validation Tests ───────────────────────────────────────────────────

def test_article_base_schema():
    """Test ArticleBase schema validation."""
    from backend.api.schemas import ArticleBase
    from backend.models.article import Article
    
    article = Article(
        id=1,
        url="https://test.com",
        title="Test",
        source_name="Source",
        topic="politics",
    )
    
    schema = ArticleBase.model_validate(article)
    assert schema.id == 1
    assert schema.url == "https://test.com"


def test_article_list_pagination():
    """Test ArticleList pagination calculation."""
    from backend.api.schemas import ArticleList, ArticleBase
    
    result = ArticleList(
        articles=[],
        total=95,
        page=2,
        page_size=20,
    )
    
    assert result.pages == 5  # ceil(95/20) = 5
    assert result.page == 2


# ── Mock API Tests (logic validation without HTTP) ────────────────────────────

def test_list_articles_filtering(db, sample_articles):
    """Test article filtering logic."""
    from sqlalchemy import select
    
    # Filter by topic
    query = select(Article).where(Article.topic == "politics")
    results = db.execute(query).scalars().all()
    assert len(results) == 5  # Half are politics
    
    # Filter by bias
    query = select(Article).where(Article.bias_label == "center-right")
    results = db.execute(query).scalars().all()
    assert len(results) == 5


def test_insights_topic_aggregation(db, sample_articles):
    """Test topic statistics aggregation."""
    from sqlalchemy import select, func
    
    query = (
        select(
            Article.topic,
            func.count(Article.id).label("count"),
            func.avg(Article.bias_score).label("avg_bias"),
        )
        .where(Article.is_processed == True)
        .group_by(Article.topic)
    )
    
    results = db.execute(query).all()
    
    # Should have 2 topics
    assert len(results) == 2
    
    # Each topic should have 5 articles
    for row in results:
        assert row.count == 5


def test_bias_distribution(db, sample_articles):
    """Test bias distribution calculation."""
    from sqlalchemy import select, func
    
    query = (
        select(
            Article.bias_label,
            func.count(Article.id),
        )
        .group_by(Article.bias_label)
    )
    
    results = db.execute(query).all()
    
    # Should have 2 bias labels
    assert len(results) == 2
    
    # Each should have 5 articles
    for label, count in results:
        assert count == 5
        assert label in ("center-left", "center-right")


def test_recommendations_logic(db, sample_articles):
    """Test basic recommendation logic."""
    from backend.services.recommendations import _jaccard_similarity
    
    # Test Jaccard similarity
    set1 = {"a", "b", "c"}
    set2 = {"b", "c", "d"}
    
    sim = _jaccard_similarity(set1, set2)
    # Intersection: {b, c} = 2
    # Union: {a, b, c, d} = 4
    # Similarity: 2/4 = 0.5
    assert sim == 0.5
    
    # Empty sets
    assert _jaccard_similarity(set(), {"a"}) == 0.0


def test_search_query_pattern(db, sample_articles):
    """Test search query construction."""
    from sqlalchemy import select
    
    search = "%politics%"
    query = (
        select(Article)
        .where(Article.title.ilike(search))
    )
    
    results = db.execute(query).scalars().all()
    
    # All articles have "politics" in title
    assert len(results) == 10


# ── Response Schema Tests ─────────────────────────────────────────────────────

def test_topic_summary_bias_label_derivation():
    """Test bias label is correctly derived from score."""
    from backend.api.schemas import TopicSummary
    
    # This would normally be done in the endpoint
    def get_label(score):
        if score < -0.4:
            return "left"
        elif score < -0.15:
            return "center-left"
        elif score > 0.4:
            return "right"
        elif score > 0.15:
            return "center-right"
        else:
            return "center"
    
    assert get_label(-0.5) == "left"
    assert get_label(-0.2) == "center-left"
    assert get_label(0.0) == "center"
    assert get_label(0.3) == "center-right"
    assert get_label(0.6) == "right"


def test_pagination_edge_cases():
    """Test pagination with edge cases."""
    from backend.api.schemas import ArticleList
    
    # Exactly divisible
    result = ArticleList(articles=[], total=100, page=1, page_size=20)
    assert result.pages == 5
    
    # With remainder
    result = ArticleList(articles=[], total=101, page=1, page_size=20)
    assert result.pages == 6
    
    # Empty result
    result = ArticleList(articles=[], total=0, page=1, page_size=20)
    assert result.pages == 0
