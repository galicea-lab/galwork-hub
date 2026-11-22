"""pm_taiga: integracja Taiga - galwork-pm - ERPNext (Etap 1)

Revision ID: b7c1d9e2f4a8
Revises: 63de9f44de14
Create Date: 2026-11-21 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c1d9e2f4a8'
down_revision: Union[str, Sequence[str], None] = '63de9f44de14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'pm_taiga_project_link',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('taiga_project_id', sa.Integer(), nullable=False),
        sa.Column('taiga_slug', sa.String(length=255), nullable=True),
        sa.Column('pm_project_id', sa.Integer(),
                  sa.ForeignKey('pm_projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('erp_project', sa.String(length=140), nullable=True),
        sa.Column('default_activity_type', sa.String(length=140), nullable=True),
        sa.Column('hours_attribute_id_task', sa.Integer(), nullable=True),
        sa.Column('hours_attribute_id_us', sa.Integer(), nullable=True),
        sa.Column('hours_attribute_name', sa.String(length=255), nullable=False),
        sa.Column('billable', sa.Boolean(), nullable=False),
        sa.Column('triage_group', sa.String(length=255), nullable=True),
        sa.Column('billing_from', sa.Date(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('webhook_id', sa.Integer(), nullable=True),
        sa.Column('last_webhook_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('timeline_cursor', sa.Integer(), nullable=True),
        sa.Column('last_reconcile_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('taiga_project_id'),
    )
    op.create_table(
        'pm_person_link',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('taiga_user_id', sa.Integer(), nullable=False),
        sa.Column('erp_employee', sa.String(length=140), nullable=False),
        sa.Column('keycloak_user_id', sa.String(length=255), nullable=True),
        sa.Column('source', sa.String(length=32), nullable=False),
        sa.Column('confirmed', sa.Boolean(), nullable=False),
        sa.Column('valid_from', sa.Date(), nullable=True),
        sa.Column('valid_to', sa.Date(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('taiga_user_id', 'erp_employee', name='uq_pm_person_link'),
    )
    op.create_index('ix_pm_person_link_taiga_user_id', 'pm_person_link', ['taiga_user_id'])
    op.create_table(
        'pm_taiga_event',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('taiga_project_id', sa.Integer(), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('dedupe_key', sa.String(length=128), nullable=False),
        sa.Column('object_type', sa.String(length=32), nullable=True),
        sa.Column('action', sa.String(length=16), nullable=True),
        sa.Column('object_id', sa.Integer(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('by_taiga_user_id', sa.Integer(), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('dedupe_key'),
    )
    op.create_index('ix_pm_taiga_event_taiga_project_id', 'pm_taiga_event', ['taiga_project_id'])
    op.create_index('ix_pm_taiga_event_status', 'pm_taiga_event', ['status'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_pm_taiga_event_status', table_name='pm_taiga_event')
    op.drop_index('ix_pm_taiga_event_taiga_project_id', table_name='pm_taiga_event')
    op.drop_table('pm_taiga_event')
    op.drop_index('ix_pm_person_link_taiga_user_id', table_name='pm_person_link')
    op.drop_table('pm_person_link')
    op.drop_table('pm_taiga_project_link')
