"""process_instance: powiązanie rodzic -> dziecko (instancje uruchamiane przez inne)

Revision ID: f3b8d2a6c104
Revises: e7a1c3d5f902
Create Date: 2026-11-13 00:00:00.000000

"""
# 2026-11-13: sendTask `startProcess` i szybkie akcje zadań uruchamiają
# osobną instancję powiązaną z rodzicem (wf/models/wf.py::ProcessInstance).
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f3b8d2a6c104'
down_revision: Union[str, Sequence[str], None] = 'e7a1c3d5f902'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('process_instance', sa.Column('parent_instance_id', sa.Uuid(), nullable=True))
    op.add_column('process_instance', sa.Column('parent_task_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_process_instance_parent', 'process_instance', 'process_instance',
                          ['parent_instance_id'], ['id'], ondelete='SET NULL')
    op.create_index('ix_pi_parent', 'process_instance', ['parent_instance_id'])


def downgrade() -> None:
    op.drop_index('ix_pi_parent', table_name='process_instance')
    op.drop_constraint('fk_process_instance_parent', 'process_instance', type_='foreignkey')
    op.drop_column('process_instance', 'parent_task_id')
    op.drop_column('process_instance', 'parent_instance_id')
