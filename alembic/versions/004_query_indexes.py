"""Add additional indexes for optimized query patterns

Revision ID: 004_query_indexes
Revises: 003_bias_confidence
Create Date: 2024-04-19

New indexes:
  - (is_processed, topic) for batch processing queries
  - (is_processed, fetched_at) for ingestion pipeline
  - (bias_label) for bias distribution queries
  - related_articles.similarity_score for top-N recommendation lookup
"""
from alembic import op
import sqlalchemy as sa

revision = '004_query_indexes'
down_revision = '003_bias_confidence'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Partial-style indexes for the most common filter combinations
    # (is_processed + topic) — used by /articles?topic=... and batch processor
    op.create_index(
        'ix_articles_processed_topic',
        'articles',
        ['is_processed', 'topic'],
        unique=False,
    )
    # (is_processed + fetched_at) — used by processing batch: ORDER BY fetched_at WHERE is_processed=False
    op.create_index(
        'ix_articles_processed_fetched',
        'articles',
        ['is_processed', 'fetched_at'],
        unique=False,
    )
    # bias_label alone — for /insights/bias-distribution GROUP BY bias_label
    op.create_index(
        'ix_articles_bias_label',
        'articles',
        ['bias_label'],
        unique=False,
    )
    # similarity_score on related_articles — for ORDER BY similarity_score DESC in recommendations
    op.create_index(
        'ix_related_similarity',
        'related_articles',
        ['similarity_score'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_related_similarity', table_name='related_articles')
    op.drop_index('ix_articles_bias_label', table_name='articles')
    op.drop_index('ix_articles_processed_fetched', table_name='articles')
    op.drop_index('ix_articles_processed_topic', table_name='articles')
