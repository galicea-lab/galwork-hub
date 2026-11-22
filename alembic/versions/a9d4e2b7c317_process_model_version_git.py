"""process_model_version: commit git, niezacommitowane zmiany, tag wydania

Revision ID: a9d4e2b7c317
Revises: a9d4e2b7c316
Create Date: 2026-11-17 00:00:00.000000

"""
# 2026-11-17: pochodzenie wersji modelu z gita (wf/services/model_git.py);
# wyzwalacz niezmienności chroni też nowe kolumny (wf/models/immutability.py).
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from wf.models.immutability import TABLES_V1, postgresql_statements

revision: str = 'a9d4e2b7c317'
down_revision: Union[str, Sequence[str], None] = 'a9d4e2b7c316'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('process_model_version', sa.Column('git_commit', sa.Text(), nullable=True))
    op.add_column('process_model_version', sa.Column('git_dirty', sa.Boolean(), nullable=True))
    op.add_column('process_model_version', sa.Column('git_tag', sa.Text(), nullable=True))
    if op.get_context().dialect.name == 'postgresql':
        # CREATE OR REPLACE funkcji wyzwalacza z nowymi kolumnami
        for statement in postgresql_statements(TABLES_V1):
            op.execute(statement)


_GIT_CONDITIONS = (
    "     OR NEW.git_commit IS DISTINCT FROM OLD.git_commit\n",
    "     OR NEW.git_dirty IS DISTINCT FROM OLD.git_dirty\n",
    "\n           OR NEW.git_tag IS DISTINCT FROM OLD.git_tag",
)


def downgrade() -> None:
    if op.get_context().dialect.name == 'postgresql':
        # funkcja wyzwalacza bez nowych kolumn - inaczej każda zmiana wersji
        # modelu kończyłaby się błędem "brak kolumny"
        for statement in postgresql_statements(TABLES_V1):
            if statement.startswith('CREATE OR REPLACE FUNCTION galwork_protect_model_version'):
                for condition in _GIT_CONDITIONS:
                    statement = statement.replace(condition, '')
                op.execute(statement)
    op.drop_column('process_model_version', 'git_tag')
    op.drop_column('process_model_version', 'git_dirty')
    op.drop_column('process_model_version', 'git_commit')
