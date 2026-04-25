"""Add bias_confidence and bias_signals_json to articles

Revision ID: 003_bias_confidence
Revises: 002_relations
Create Date: 2024-04-19

"""
from alembic import op
import sqlalchemy as sa

revision = '003_bias_confidence'
down_revision = '002_relations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('articles', sa.Column('bias_confidence', sa.Float(), nullable=True))
    op.add_column('articles', sa.Column('bias_signals_json', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('articles', 'bias_signals_json')
    op.drop_column('articles', 'bias_confidence')
