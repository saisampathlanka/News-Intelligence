"""
Integration test: full pipeline from raw fetch to processed articles.
Run: python backend/tests/test_integration.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.article import Article
from backend.models.schemas import RawArticle
from backend.services.ingestion import _save_articles
from backend.services.processing import process_batch


def test_full_pipeline():
    """Simulate: fetch → save → process → verify."""
    
    # Setup in-memory DB
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    
    # ── Step 1: Simulate ingestion ────────────────────────────────
    print("\n1. Simulating article ingestion...")
    raw_articles = [
        RawArticle(
            url="https://example.com/politics1",
            title="President announces new healthcare policy for universal coverage",
            description="Government unveils progressive plan to address inequality in healthcare system",
            content="The new policy aims to provide universal healthcare coverage for all citizens...",
            source_name="Test News",
            source_type="rss",
            published_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        RawArticle(
            url="https://example.com/business1",
            title="Tech company stock rallies on strong earnings report",
            description="Market responds positively to corporate revenue growth and innovation",
            content="The company reported record profits driven by AI technology investments...",
            source_name="Business Daily",
            source_type="rss",
            published_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        ),
        RawArticle(
            url="https://example.com/world1",
            title="International climate summit reaches historic agreement",
            description="Nations commit to reducing carbon emissions and renewable energy targets",
            content="World leaders agreed to binding climate commitments at the global summit...",
            source_name="World News",
            source_type="rss",
            published_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
        ),
    ]
    
    saved, dupes = _save_articles(db, raw_articles)
    print(f"   → Saved {saved} articles, {dupes} duplicates")
    assert saved == 3
    
    # ── Step 2: Process articles ──────────────────────────────────
    print("\n2. Processing articles...")
    result = process_batch(db, batch_size=10)
    print(f"   → Processed {result['processed']} articles in {result['duration_sec']}s")
    assert result["processed"] == 3
    
    # ── Step 3: Verify processing results ─────────────────────────
    print("\n3. Verifying results...")
    articles = db.query(Article).all()
    
    for article in articles:
        print(f"\n   Article: {article.title[:60]}...")
        print(f"   → Topic: {article.topic}")
        print(f"   → Bias: {article.bias_label} ({article.bias_score})")
        print(f"   → Sentiment: {article.sentiment_score}")
        print(f"   → Processed: {article.is_processed}")
        
        # Assertions
        assert article.topic is not None
        assert article.bias_score is not None
        assert article.bias_label is not None
        assert article.sentiment_score is not None
        assert article.is_processed == True
        assert article.entities_json is not None
        assert article.keywords_json is not None
    
    # ── Step 4: Verify specific expectations ──────────────────────
    print("\n4. Checking expected classifications...")
    
    # Politics article should be classified as politics or health
    politics_article = db.query(Article).filter(Article.url.contains("politics1")).first()
    assert politics_article.topic in ("politics", "health"), f"Expected politics/health, got {politics_article.topic}"
    assert politics_article.bias_label in ("left", "center-left"), f"Progressive language should lean left"
    print(f"   ✓ Politics article correctly classified as {politics_article.topic}")
    
    # Business article should be business or technology
    business_article = db.query(Article).filter(Article.url.contains("business1")).first()
    assert business_article.topic in ("business", "technology"), f"Expected business/tech, got {business_article.topic}"
    print(f"   ✓ Business article correctly classified as {business_article.topic}")
    
    # Climate article should be environment or world
    climate_article = db.query(Article).filter(Article.url.contains("world1")).first()
    assert climate_article.topic in ("environment", "world", "science"), f"Expected environment/world, got {climate_article.topic}"
    print(f"   ✓ Climate article correctly classified as {climate_article.topic}")
    
    db.close()
    print("\n✅ Full pipeline integration test passed!")


if __name__ == "__main__":
    test_full_pipeline()
