"""purchase_request contact_id nullable

Revision ID: f2a6b5c0e921
Revises: d18dcc201714
Create Date: 2026-10-20 00:00:00.000001

Składający wniosek zakupowy nie musi znać dostawcy w chwili złożenia -
zob. wf/models/wf.py::PurchaseRequest.contact_id, galwork-book
czesc-3-obszary/dzialy/zakupy.md.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2a6b5c0e921'
down_revision: Union[str, Sequence[str], None] = 'd18dcc201714'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('purchase_request', 'contact_id', nullable=True)


def downgrade() -> None:
    op.alter_column('purchase_request', 'contact_id', nullable=False)
