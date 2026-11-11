"""kalendarz: subskrypcja ICS (token osobisty)

Revision ID: c5d2e8f13a47
Revises: b4e81f2c7d90
Create Date: 2026-11-10 00:00:00.000000

"""
# 2026-11-10: tabela subskrypcji kalendarza (kalendarz/models.py).
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'c5d2e8f13a47'
down_revision: Union[str, Sequence[str], None] = 'b4e81f2c7d90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('kalendarz_subskrypcja',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('szczegolowosc', sa.String(length=16), nullable=False),
    sa.Column('rola', sa.String(length=16), nullable=True),
    sa.Column('utworzono', sa.DateTime(), nullable=False),
    sa.Column('ostatnie_pobranie', sa.DateTime(), nullable=True),
    sa.Column('liczba_pobran', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_kalendarz_subskrypcja_user_id', 'kalendarz_subskrypcja', ['user_id'], unique=True)
    op.create_index('ix_kalendarz_subskrypcja_token_hash', 'kalendarz_subskrypcja', ['token_hash'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_kalendarz_subskrypcja_token_hash', table_name='kalendarz_subskrypcja')
    op.drop_index('ix_kalendarz_subskrypcja_user_id', table_name='kalendarz_subskrypcja')
    op.drop_table('kalendarz_subskrypcja')
