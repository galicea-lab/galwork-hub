"""wyzwalacze niezmienności: wersje modeli i dokumentów, dziennik zdarzeń

Revision ID: a9d4e2b7c316
Revises: a9d4e2b7c315
Create Date: 2026-11-17 00:00:00.000000

"""
# 2026-11-17: ochrona przed zmianą danych, na które powołują się akty
# i dziennik (wf/models/immutability.py). Wyjątek dla narzędzi administratora:
# SET LOCAL galwork.allow_purge = 'on'.
from typing import Sequence, Union

from alembic import op

from wf.models.immutability import TABLES_V1, postgresql_drop_statements, postgresql_statements

revision: str = 'a9d4e2b7c316'
down_revision: Union[str, Sequence[str], None] = 'a9d4e2b7c315'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_context().dialect.name != 'postgresql':
        return
    for statement in postgresql_statements(TABLES_V1):
        op.execute(statement)


def downgrade() -> None:
    if op.get_context().dialect.name != 'postgresql':
        return
    for statement in postgresql_drop_statements():
        op.execute(statement)
