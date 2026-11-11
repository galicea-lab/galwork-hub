"""purchase_request

Revision ID: d18dcc201714
Revises: 0b7572a1902f
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd18dcc201714'
down_revision: Union[str, Sequence[str], None] = '0b7572a1902f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('purchase_request',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('process_instance_id', sa.Uuid(), nullable=True),
    sa.Column('tender_process_instance_id', sa.Uuid(), nullable=True),
    sa.Column('contact_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.Text(), nullable=False),
    sa.Column('justification', sa.Text(), nullable=True),
    sa.Column('amount_estimated', sa.Numeric(14, 2), nullable=False),
    sa.Column('currency', sa.Text(), server_default=sa.text("'PLN'"), nullable=False),
    sa.Column('expense_type', sa.Enum('operating', 'investment', name='purchase_request_expense_type_check', native_enum=False), nullable=False),
    sa.Column('purchase_kind', sa.Enum('service', 'stock', name='purchase_request_purchase_kind_check', native_enum=False), nullable=False),
    sa.Column('project_code', sa.Text(), nullable=True),
    sa.Column('requires_investment_approval', sa.Boolean(), nullable=False),
    sa.Column('requires_project_owner_approval', sa.Boolean(), nullable=False),
    sa.Column('requires_additional_approval', sa.Boolean(), nullable=False),
    sa.Column('requires_tender', sa.Boolean(), nullable=False),
    sa.Column('requires_warehouse_receipt', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contact.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['process_instance_id'], ['process_instance.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tender_process_instance_id'], ['process_instance.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_purchase_request_contact', 'purchase_request', ['contact_id'], unique=False)
    op.create_index('ix_purchase_request_created_at', 'purchase_request', ['created_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_purchase_request_created_at', table_name='purchase_request')
    op.drop_index('ix_purchase_request_contact', table_name='purchase_request')
    op.drop_table('purchase_request')
