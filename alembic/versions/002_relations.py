"""Add related_articles and topic_stats tables

Revision ID: 002_relations
Revises: 001_initial
Create Date: 2024-01-02 00:00:00

"""
from alembic import op
import sqlalchemy as sa

revision = '002_relations'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Related articles table
    op.create_table(
        'related_articles',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=False),
        sa.Column('related_id', sa.Integer(), nullable=False),
        sa.Column('similarity_score', sa.Float(), nullable=False),
        sa.Column('relation_type', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['related_id'], ['articles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_related_article_id', 'related_articles', ['article_id'], unique=False)
    op.create_index('ix_related_related_id', 'related_articles', ['related_id'], unique=False)
    op.create_index('ix_related_article_score', 'related_articles', ['article_id', 'similarity_score'], unique=False)
    op.create_index('ix_related_unique', 'related_articles', ['article_id', 'related_id'], unique=True)
    
    # Topic stats table
    op.create_table(
        'topic_stats',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('topic', sa.String(length=128), nullable=False),
        sa.Column('article_count', sa.Integer(), nullable=True),
        sa.Column('avg_bias_score', sa.Float(), nullable=True),
        sa.Column('avg_sentiment', sa.Float(), nullable=True),
        sa.Column('last_updated', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_topic_stats_topic', 'topic_stats', ['topic'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_topic_stats_topic', table_name='topic_stats')
    op.drop_table('topic_stats')
    
    op.drop_index('ix_related_unique', table_name='related_articles')
    op.drop_index('ix_related_article_score', table_name='related_articles')
    op.drop_index('ix_related_related_id', table_name='related_articles')
    op.drop_index('ix_related_article_id', table_name='related_articles')
    op.drop_table('related_articles')
