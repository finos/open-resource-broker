"""backfill machine provider_api

Revision ID: a3e9c1d0b4f2
Revises: f6d2ba73f23c
Create Date: 2026-06-27 00:00:00.000000

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3e9c1d0b4f2"
down_revision: str | Sequence[str] | None = "f6d2ba73f23c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Backfill NULL provider_api from the associated request, then enforce NOT NULL."""
    bind = op.get_bind()

    # Backfill from the associated request when possible.  The COALESCE
    # ensures rows that have no matching request (orphaned machines) get an
    # empty-string sentinel rather than staying NULL — operators can grep
    # logs written by the read-time backfill for machines that still show
    # up with an empty string after this migration.
    bind.execute(
        sa.text("""
            UPDATE machines
            SET provider_api = COALESCE(
                (SELECT provider_api FROM requests WHERE requests.request_id = machines.request_id),
                ''
            )
            WHERE provider_api IS NULL OR provider_api = ''
        """)
    )

    # Alter the column to NOT NULL now that every row has a value.
    # op.batch_alter_table is used so this works on SQLite (which does not
    # support ALTER COLUMN directly) as well as PostgreSQL / MySQL.
    with op.batch_alter_table("machines") as batch_op:
        batch_op.alter_column("provider_api", nullable=False)


def downgrade() -> None:
    """Downgrade is not supported."""
    raise NotImplementedError("backfill is one-way; data restore needs backup")
