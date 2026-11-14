"""human_task: zadania podprocesów (callActivity między modelami)

Revision ID: e7a1c3d5f902
Revises: c5d2e8f13a47
Create Date: 2026-11-13 00:00:00.000000

"""
# 2026-11-13: proces BPMN zadania i wersja modelu z formularzem zadania
# podprocesu wywołanego z innego modelu (wf/models/wf.py::HumanTask).
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e7a1c3d5f902'
down_revision: Union[str, Sequence[str], None] = 'c5d2e8f13a47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('human_task', sa.Column('bpmn_process_id', sa.Text(), nullable=True))
    op.add_column('human_task', sa.Column('form_version_id', sa.Uuid(), nullable=True))
    op.create_foreign_key('fk_human_task_form_version', 'human_task', 'process_model_version',
                          ['form_version_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_human_task_form_version', 'human_task', type_='foreignkey')
    op.drop_column('human_task', 'form_version_id')
    op.drop_column('human_task', 'bpmn_process_id')
