"""pm_taiga_ledger: słownik rodzajów prac, stan pozycji Taigi, dziennik prac, okresy (Etap 2)

Revision ID: c4e2a8f1d3b6
Revises: b7c1d9e2f4a8
Create Date: 2026-11-21 14:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4e2a8f1d3b6'
down_revision: Union[str, Sequence[str], None] = 'b7c1d9e2f4a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

HOURS = sa.Numeric(10, 2)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('pm_taiga_project_link', sa.Column('baseline_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'pm_activity_map',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('taiga_project_id', sa.Integer(), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('taiga_object_id', sa.Integer(), nullable=False),
        sa.Column('activity_type', sa.String(length=140), nullable=True),
        sa.Column('erp_project', sa.String(length=140), nullable=True),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('valid_from', sa.DateTime(timezone=True), nullable=False),
        sa.Column('valid_to', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
    )
    op.create_index('ix_pm_activity_map_lookup', 'pm_activity_map',
                    ['taiga_project_id', 'scope', 'taiga_object_id', 'valid_to'])
    op.create_table(
        'pm_taiga_item',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('taiga_id', sa.Integer(), nullable=False),
        sa.Column('taiga_project_id', sa.Integer(), nullable=False),
        sa.Column('ref', sa.Integer(), nullable=True),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('user_story_id', sa.Integer(), nullable=True),
        sa.Column('epic_ids', sa.JSON(), nullable=True),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('assigned_users', sa.JSON(), nullable=True),
        sa.Column('assigned_role_id', sa.Integer(), nullable=True),
        sa.Column('fallback_user_id', sa.Integer(), nullable=True),
        sa.Column('is_closed', sa.Boolean(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('hours_total', HOURS, nullable=False),
        sa.Column('baseline_hours', HOURS, nullable=False),
        sa.Column('activity_type_resolved', sa.String(length=140), nullable=True),
        sa.Column('erp_project_resolved', sa.String(length=140), nullable=True),
        sa.Column('erp_employee_resolved', sa.String(length=140), nullable=True),
        sa.Column('last_change_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_event_id', sa.Integer(), nullable=True),
        sa.Column('deleted', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('kind', 'taiga_id', name='uq_pm_taiga_item'),
    )
    op.create_index('ix_pm_taiga_item_taiga_project_id', 'pm_taiga_item', ['taiga_project_id'])
    op.create_index('ix_pm_taiga_item_user_story_id', 'pm_taiga_item', ['user_story_id'])
    op.create_table(
        'pm_work_ledger',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('item_id', sa.Integer(), sa.ForeignKey('pm_taiga_item.id'), nullable=False),
        sa.Column('taiga_project_id', sa.Integer(), nullable=False),
        sa.Column('taiga_user_id', sa.Integer(), nullable=True),
        sa.Column('erp_employee', sa.String(length=140), nullable=True),
        sa.Column('erp_project', sa.String(length=140), nullable=True),
        sa.Column('activity_type', sa.String(length=140), nullable=True),
        sa.Column('work_week', sa.String(length=8), nullable=False),
        sa.Column('origin_week', sa.String(length=8), nullable=False),
        sa.Column('hours_delta', HOURS, nullable=False),
        sa.Column('reason', sa.String(length=32), nullable=False),
        sa.Column('event_id', sa.Integer(), nullable=True),
        sa.Column('taiga_changed_by', sa.Integer(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('ix_pm_work_ledger_item_id', 'pm_work_ledger', ['item_id'])
    op.create_index('ix_pm_work_ledger_taiga_project_id', 'pm_work_ledger', ['taiga_project_id'])
    op.create_index('ix_pm_work_ledger_aggregate', 'pm_work_ledger',
                    ['work_week', 'erp_employee', 'erp_project', 'activity_type'])
    op.create_table(
        'pm_period_close',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('month', sa.String(length=7), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_by', sa.String(length=255), nullable=True),
        sa.UniqueConstraint('month'),
    )
    op.create_table(
        'pm_week_approval',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('taiga_project_id', sa.Integer(), nullable=False),
        sa.Column('week', sa.String(length=8), nullable=False),
        sa.Column('approved_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('approved_by', sa.String(length=255), nullable=True),
        sa.UniqueConstraint('taiga_project_id', 'week', name='uq_pm_week_approval'),
    )
    if op.get_context().dialect.name == 'postgresql':
        op.execute(
            "CREATE TRIGGER trg_pm_work_ledger_append_only BEFORE UPDATE OR DELETE ON pm_work_ledger "
            "FOR EACH ROW EXECUTE FUNCTION galwork_protect_append_only()")


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_context().dialect.name == 'postgresql':
        op.execute("DROP TRIGGER IF EXISTS trg_pm_work_ledger_append_only ON pm_work_ledger")
    op.drop_table('pm_week_approval')
    op.drop_table('pm_period_close')
    op.drop_index('ix_pm_work_ledger_aggregate', table_name='pm_work_ledger')
    op.drop_index('ix_pm_work_ledger_taiga_project_id', table_name='pm_work_ledger')
    op.drop_index('ix_pm_work_ledger_item_id', table_name='pm_work_ledger')
    op.drop_table('pm_work_ledger')
    op.drop_index('ix_pm_taiga_item_user_story_id', table_name='pm_taiga_item')
    op.drop_index('ix_pm_taiga_item_taiga_project_id', table_name='pm_taiga_item')
    op.drop_table('pm_taiga_item')
    op.drop_index('ix_pm_activity_map_lookup', table_name='pm_activity_map')
    op.drop_table('pm_activity_map')
    op.drop_column('pm_taiga_project_link', 'baseline_at')
