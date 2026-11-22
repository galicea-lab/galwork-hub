"""process_discussion: linki do dyskusji o sprawie w komunikatorze

Revision ID: a9d4e2b7c319
Revises: a9d4e2b7c318
Create Date: 2026-11-17 00:00:00.000000

"""
# 2026-11-17: link do dyskusji (domyślnie Mattermost) - wf/services/discussions.py
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a9d4e2b7c319'
down_revision: Union[str, Sequence[str], None] = 'a9d4e2b7c318'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'process_discussion',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('process_instance_id', sa.Uuid(), nullable=False),
        sa.Column('human_task_id', sa.Uuid(), nullable=True),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['process_instance_id'], ['process_instance.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['human_task_id'], ['human_task.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_discussion_instance', 'process_discussion',
                    ['process_instance_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_discussion_instance', table_name='process_discussion')
    op.drop_table('process_discussion')
