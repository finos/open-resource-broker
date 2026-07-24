"""add fulfilment state-machine columns

Adds four nullable columns to the ``requests`` table:
  - deadline_at            (ISO-8601 TEXT)
  - partial_since          (ISO-8601 TEXT)
  - last_transition_at     (ISO-8601 TEXT)
  - fulfilment_diagnostic  (JSON-encoded TEXT)

All are nullable with no server_default: the domain aggregate supplies ``None``
for legacy rows, so this migration is backward-compatible and fully reversible.

Revision ID: 55e3b5553d98
Revises: f6d2ba73f23c
Create Date: 2026-07-20

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# These four module globals are Alembic's required migration API — Alembic reads
# them by name to build the revision graph, so they are never referenced from
# within this module. The migration versions directory is excluded from CodeQL
# code-quality scanning (see .github/codeql/codeql-config.yml) precisely because
# these mandatory globals are a universal py/unused-global-variable false positive.
revision: str = "55e3b5553d98"
down_revision: str | Sequence[str] | None = "f6d2ba73f23c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema — add fulfilment columns (batch mode for SQLite)."""
    with op.batch_alter_table("requests") as batch:
        batch.add_column(sa.Column("deadline_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("partial_since", sa.Text(), nullable=True))
        batch.add_column(sa.Column("last_transition_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("fulfilment_diagnostic", sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema — drop fulfilment columns (batch mode for SQLite)."""
    with op.batch_alter_table("requests") as batch:
        batch.drop_column("fulfilment_diagnostic")
        batch.drop_column("last_transition_at")
        batch.drop_column("partial_since")
        batch.drop_column("deadline_at")
