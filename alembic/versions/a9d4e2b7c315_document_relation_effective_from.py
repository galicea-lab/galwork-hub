"""document_version.effective_from + document_relation (akty: zmiany i uchylenia)

Revision ID: a9d4e2b7c315
Revises: f3b8d2a6c104
Create Date: 2026-11-17 00:00:00.000000

"""
# 2026-11-17: wersje treści aktów wewnętrznych - data wejścia w życie
# redakcji i relacje "zmienia"/"uchyla" (wf/models/wf.py, wf/services/legal_acts.py).
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a9d4e2b7c315'
down_revision: Union[str, Sequence[str], None] = 'f3b8d2a6c104'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('document_version', sa.Column('effective_from', sa.Date(), nullable=True))
    op.create_table(
        'document_relation',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('source_document_id', sa.Uuid(), nullable=False),
        sa.Column('target_document_id', sa.Uuid(), nullable=False),
        sa.Column('relation_type', sa.Enum('amends', 'repeals', name='document_relation_type_check',
                                           native_enum=False, create_constraint=True, length=32),
                  nullable=False),
        sa.Column('effective_from', sa.Date(), nullable=False),
        sa.Column('target_version_no', sa.Integer(), nullable=True),
        sa.Column('process_instance_id', sa.Uuid(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.CheckConstraint('source_document_id <> target_document_id',
                           name='ck_document_relation_self'),
        sa.ForeignKeyConstraint(['source_document_id'], ['document.id']),
        sa.ForeignKeyConstraint(['target_document_id'], ['document.id']),
        sa.ForeignKeyConstraint(['process_instance_id'], ['process_instance.id'],
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_document_id', 'target_document_id', 'relation_type',
                            name='uq_document_relation'),
    )
    op.create_index('ix_document_relation_target', 'document_relation',
                    ['target_document_id', 'effective_from'])


def downgrade() -> None:
    op.drop_index('ix_document_relation_target', table_name='document_relation')
    op.drop_table('document_relation')
    op.drop_column('document_version', 'effective_from')
