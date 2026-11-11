"""konto: sesje weryfikacji przez login.gov.pl

Revision ID: b4e81f2c7d90
Revises: a7c3e9d41b26
Create Date: 2026-11-09 12:00:00.000000

"""
# Tabela konto_wk_session (konto/models.py, konto/wk.py).
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b4e81f2c7d90'
down_revision: Union[str, Sequence[str], None] = 'a7c3e9d41b26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('konto_wk_session',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('authn_request_id', sa.String(length=64), nullable=False),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('ticket', sa.String(length=64), nullable=True),
    sa.Column('verified_at', sa.DateTime(), nullable=True),
    sa.Column('identity_kind', sa.String(length=8), nullable=True),
    sa.Column('identity_hash', sa.String(length=64), nullable=True),
    sa.Column('first_name', sa.String(length=100), nullable=True),
    sa.Column('last_name', sa.String(length=100), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('ticket')
    )
    op.create_index('ix_konto_wk_session_ip_hash', 'konto_wk_session', ['ip_hash'], unique=False)
    op.create_index('ix_konto_wk_session_created_at', 'konto_wk_session', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_konto_wk_session_created_at', table_name='konto_wk_session')
    op.drop_index('ix_konto_wk_session_ip_hash', table_name='konto_wk_session')
    op.drop_table('konto_wk_session')
