"""process_rollout (wdrożenia obiegów) + tryb użycia sprawy

Revision ID: a9d4e2b7c318
Revises: a9d4e2b7c317
Create Date: 2026-11-17 00:00:00.000000

"""
# 2026-11-17: tryb użycia obiegu (testowy/pomocniczy/produkcyjny) w jednostkach
# organizacyjnych na podstawie aktu (wf/services/rollouts.py); wyzwalacze
# niezmienności wdrożeń i trybu sprawy (wf/models/immutability.py).
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from wf.models.immutability import TABLES_V2, postgresql_statements

revision: str = 'a9d4e2b7c318'
down_revision: Union[str, Sequence[str], None] = 'a9d4e2b7c317'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TRIGGERS = ('process_rollout', 'process_instance')


def upgrade() -> None:
    op.create_table(
        'process_rollout',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('process_model_id', sa.Uuid(), nullable=False),
        sa.Column('mode', sa.Enum('test', 'auxiliary', 'production', name='process_rollout_mode_check',
                                  native_enum=False, create_constraint=True, length=32),
                  nullable=False),
        sa.Column('org_unit_ids', sa.JSON().with_variant(JSONB(), 'postgresql'), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=False),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('act_document_id', sa.Uuid(), nullable=False),
        sa.Column('act_version_no', sa.Integer(), nullable=False),
        sa.Column('previous_procedure', sa.Text(), nullable=True),
        sa.Column('process_instance_id', sa.Uuid(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('closed_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('closed_by', sa.Integer(), nullable=True),
        sa.Column('close_reason', sa.Text(), nullable=True),
        sa.CheckConstraint("mode <> 'test' OR valid_to IS NOT NULL", name='ck_rollout_test_has_end'),
        sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_rollout_period'),
        sa.ForeignKeyConstraint(['process_model_id'], ['process_model.id']),
        sa.ForeignKeyConstraint(['act_document_id'], ['document.id']),
        sa.ForeignKeyConstraint(['process_instance_id'], ['process_instance.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['closed_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_rollout_model', 'process_rollout', ['process_model_id', 'valid_from'])
    op.add_column('process_instance', sa.Column('rollout_id', sa.Uuid(), nullable=True))
    op.add_column('process_instance', sa.Column('usage_mode', sa.Text(), nullable=True))
    op.create_foreign_key('fk_process_instance_rollout', 'process_instance', 'process_rollout',
                          ['rollout_id'], ['id'])
    if op.get_context().dialect.name == 'postgresql':
        for statement in postgresql_statements(TABLES_V2):
            op.execute(statement)


def downgrade() -> None:
    if op.get_context().dialect.name == 'postgresql':
        for table in _NEW_TRIGGERS:
            op.execute(f'DROP TRIGGER IF EXISTS trg_{table}_protect ON {table}')
        op.execute('DROP FUNCTION IF EXISTS galwork_protect_instance_mode()')
        op.execute('DROP FUNCTION IF EXISTS galwork_protect_rollout()')
    op.drop_constraint('fk_process_instance_rollout', 'process_instance', type_='foreignkey')
    op.drop_column('process_instance', 'usage_mode')
    op.drop_column('process_instance', 'rollout_id')
    op.drop_index('ix_rollout_model', table_name='process_rollout')
    op.drop_table('process_rollout')
