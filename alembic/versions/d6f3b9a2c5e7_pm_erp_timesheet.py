"""pm_erp_timesheet: synchronizacja dziennika prac z Timesheet ERPNext (Etap 3)

Revision ID: d6f3b9a2c5e7
Revises: c4e2a8f1d3b6
Create Date: 2026-11-21 18:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd6f3b9a2c5e7'
down_revision: Union[str, Sequence[str], None] = 'c4e2a8f1d3b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('pm_week_approval', sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'pm_erp_timesheet',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('erp_employee', sa.String(length=140), nullable=False),
        sa.Column('erp_project', sa.String(length=140), nullable=False),
        sa.Column('week', sa.String(length=8), nullable=False),
        sa.Column('galwork_ref', sa.String(length=255), nullable=False),
        sa.Column('timesheet_name', sa.String(length=140), nullable=True),
        sa.Column('amended_from', sa.String(length=140), nullable=True),
        sa.Column('docstatus', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('erp_error', sa.Text(), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('erp_employee', 'erp_project', 'week', name='uq_pm_erp_timesheet'),
        sa.UniqueConstraint('galwork_ref'),
    )
    op.create_table(
        'pm_erp_posting',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('erp_timesheet_id', sa.Integer(), sa.ForeignKey('pm_erp_timesheet.id'), nullable=False),
        sa.Column('timesheet_name', sa.String(length=140), nullable=False),
        sa.Column('activity_type', sa.String(length=140), nullable=False),
        sa.Column('hours', sa.Numeric(10, 2), nullable=False),
        sa.Column('ledger_max_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('timesheet_name', 'activity_type', name='uq_pm_erp_posting'),
    )
    op.create_index('ix_pm_erp_posting_erp_timesheet_id', 'pm_erp_posting', ['erp_timesheet_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_pm_erp_posting_erp_timesheet_id', table_name='pm_erp_posting')
    op.drop_table('pm_erp_posting')
    op.drop_table('pm_erp_timesheet')
    op.drop_column('pm_week_approval', 'revoked_at')
