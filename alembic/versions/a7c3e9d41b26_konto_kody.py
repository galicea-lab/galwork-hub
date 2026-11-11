"""konto: kody jednorazowe (przepustki i pula operacji)

Revision ID: a7c3e9d41b26
Revises: 9af4a45f790f
Create Date: 2026-11-09 00:00:00.000000

"""
# 2026-11-09: tabele usługi kodów (konto/models.py).
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a7c3e9d41b26'
down_revision: Union[str, Sequence[str], None] = '9af4a45f790f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('konto_ticket',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('channel', sa.String(length=8), nullable=False),
    sa.Column('destination_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=True),
    sa.Column('code_hash', sa.String(length=64), nullable=True),
    sa.Column('ip_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('last_sent_at', sa.DateTime(), nullable=True),
    sa.Column('sends', sa.Integer(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_konto_ticket_destination_hash', 'konto_ticket', ['destination_hash'], unique=False)
    op.create_index('ix_konto_ticket_ip_hash', 'konto_ticket', ['ip_hash'], unique=False)
    op.create_index('ix_konto_ticket_created_at', 'konto_ticket', ['created_at'], unique=False)
    op.create_table('konto_operation',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('destination_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_konto_operation_destination_hash', 'konto_operation', ['destination_hash'], unique=False)
    op.create_index('ix_konto_operation_user_id', 'konto_operation', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_konto_operation_user_id', table_name='konto_operation')
    op.drop_index('ix_konto_operation_destination_hash', table_name='konto_operation')
    op.drop_table('konto_operation')
    op.drop_index('ix_konto_ticket_created_at', table_name='konto_ticket')
    op.drop_index('ix_konto_ticket_ip_hash', table_name='konto_ticket')
    op.drop_index('ix_konto_ticket_destination_hash', table_name='konto_ticket')
    op.drop_table('konto_ticket')
