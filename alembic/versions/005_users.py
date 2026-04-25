"""Create users table

Revision ID: 005_users
Revises: 004_query_indexes
Create Date: 2024-04-20
"""
from alembic import op
import sqlalchemy as sa

revision = '005_users'
down_revision = '004_query_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id',              sa.Integer(),     primary_key=True, autoincrement=True),
        sa.Column('email',           sa.String(256),   nullable=False),
        sa.Column('hashed_password', sa.String(256),   nullable=False),
        sa.Column('role',            sa.String(32),    nullable=False, server_default='viewer'),
        sa.Column('api_key',         sa.String(64),    nullable=True),
        sa.Column('is_active',       sa.Boolean(),     nullable=False, server_default='1'),
        sa.Column('created_at',      sa.DateTime(),    server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_login',      sa.DateTime(),    nullable=True),
    )
    op.create_index('ix_users_email',        'users', ['email'],               unique=True)
    op.create_index('ix_users_api_key',      'users', ['api_key'],             unique=True)
    op.create_index('ix_users_email_active', 'users', ['email', 'is_active'],  unique=False)


def downgrade() -> None:
    op.drop_index('ix_users_email_active', table_name='users')
    op.drop_index('ix_users_api_key',      table_name='users')
    op.drop_index('ix_users_email',        table_name='users')
    op.drop_table('users')
