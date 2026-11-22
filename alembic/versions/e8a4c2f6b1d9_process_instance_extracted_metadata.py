"""process_instance.extracted_metadata: kolumny list rejestrów (H1)

Revision ID: e8a4c2f6b1d9
Revises: d6f3b9a2c5e7
Create Date: 2026-11-22 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e8a4c2f6b1d9'
down_revision: Union[str, Sequence[str], None] = 'd6f3b9a2c5e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    is_pg = op.get_bind().dialect.name == 'postgresql'
    op.add_column('process_instance', sa.Column(
        'extracted_metadata',
        sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
        nullable=False,
        server_default=sa.text("'{}'::jsonb") if is_pg else sa.text("'{}'")))
    if is_pg:
        op.create_index('ix_pi_extracted_metadata', 'process_instance', ['extracted_metadata'],
                        postgresql_using='gin')


def downgrade() -> None:
    """Downgrade schema."""
    if op.get_bind().dialect.name == 'postgresql':
        op.drop_index('ix_pi_extracted_metadata', table_name='process_instance')
    op.drop_column('process_instance', 'extracted_metadata')
