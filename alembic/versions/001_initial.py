"""Initial schema: articles table

Revision ID: 001_initial
Revises: 
Create Date: 2024-01-01 00:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'articles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('url', sa.String(length=2048), nullable=False),
        sa.Column('title', sa.String(length=512), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('source_name', sa.String(length=128), nullable=False),
        sa.Column('source_type', sa.String(length=32), nullable=True),
        sa.Column('author', sa.String(length=256), nullable=True),
        sa.Column('language', sa.String(length=8), nullable=True),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('is_processed', sa.Boolean(), nullable=True),
        sa.Column('topic', sa.String(length=128), nullable=True),
        sa.Column('bias_score', sa.Float(), nullable=True),
        sa.Column('bias_label', sa.String(length=32), nullable=True),
        sa.Column('sentiment_score', sa.Float(), nullable=True),
        sa.Column('entities_json', sa.Text(), nullable=True),
        sa.Column('keywords_json', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Indexes for performance
    op.create_index('ix_articles_content_hash', 'articles', ['content_hash'], unique=True)
    op.create_index('ix_articles_url', 'articles', ['url'], unique=True)
    op.create_index('ix_articles_source_name', 'articles', ['source_name'], unique=False)
    op.create_index('ix_articles_published_at', 'articles', ['published_at'], unique=False)
    op.create_index('ix_articles_is_processed', 'articles', ['is_processed'], unique=False)
    op.create_index('ix_articles_topic', 'articles', ['topic'], unique=False)
    op.create_index('ix_articles_topic_published', 'articles', ['topic', 'published_at'], unique=False)
    op.create_index('ix_articles_source_published', 'articles', ['source_name', 'published_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_articles_source_published', table_name='articles')
    op.drop_index('ix_articles_topic_published', table_name='articles')
    op.drop_index('ix_articles_topic', table_name='articles')
    op.drop_index('ix_articles_is_processed', table_name='articles')
    op.drop_index('ix_articles_published_at', table_name='articles')
    op.drop_index('ix_articles_source_name', table_name='articles')
    op.drop_index('ix_articles_url', table_name='articles')
    op.drop_index('ix_articles_content_hash', table_name='articles')
    op.drop_table('articles')
