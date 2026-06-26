"""backfill provider_api on existing machines and enforce NOT NULL

Machine.provider_api was promoted from Optional[str] to a required str on
the domain aggregate. Existing deployments may have rows with provider_api
NULL — those rows fail aggregate validation at load time. This migration:

  1. Backfills provider_api from the originating request linkage
     (machines.request_id -> requests.provider_api, falling back to the
     return-request linkage).
  2. Deletes rows that cannot be resolved either way — the aggregate
     could never load them anyway, so single-id lookups would 500
     forever otherwise. Quarantined data is preserved in
     machines_quarantine_provider_api_null for operator inspection.
  3. Tightens the SQL column to NOT NULL so future inserts cannot
     drift back out of the invariant.

All passes are idempotent.

Revision ID: 8a3f1c5d9e21
Revises: f6d2ba73f23c
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8a3f1c5d9e21"
down_revision: Union[str, Sequence[str], None] = "f6d2ba73f23c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Backfill, quarantine orphans, then enforce NOT NULL."""
    # Pass 1 — acquire linkage.
    op.execute(
        """
        UPDATE machines
        SET provider_api = (
            SELECT requests.provider_api
            FROM requests
            WHERE requests.request_id = machines.request_id
              AND requests.provider_api IS NOT NULL
        )
        WHERE machines.provider_api IS NULL
          AND machines.request_id IS NOT NULL
        """
    )
    # Pass 2 — return-request linkage.
    op.execute(
        """
        UPDATE machines
        SET provider_api = (
            SELECT requests.provider_api
            FROM requests
            WHERE requests.request_id = machines.return_request_id
              AND requests.provider_api IS NOT NULL
        )
        WHERE machines.provider_api IS NULL
          AND machines.return_request_id IS NOT NULL
        """
    )

    # Pass 3 — quarantine unresolvable rows so single-id lookups don't 500.
    # CREATE TABLE IF NOT EXISTS keeps the migration idempotent across reruns.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS machines_quarantine_provider_api_null AS
        SELECT * FROM machines WHERE 0 = 1
        """
    )
    op.execute(
        """
        INSERT INTO machines_quarantine_provider_api_null
        SELECT * FROM machines WHERE provider_api IS NULL
        """
    )
    op.execute("DELETE FROM machines WHERE provider_api IS NULL")

    # Pass 4 — apply NOT NULL constraint. Batch op so SQLite and Postgres
    # both work; SQLite needs a table rebuild to add a NOT NULL.
    with op.batch_alter_table("machines") as batch_op:
        batch_op.alter_column(
            "provider_api",
            existing_type=sa.String(length=255),
            nullable=False,
        )


def downgrade() -> None:
    """Revert the NOT NULL constraint and restore quarantined rows.

    The actual backfilled values are not reverted — there is no way to know
    which rows were NULL before the backfill ran.
    """
    with op.batch_alter_table("machines") as batch_op:
        batch_op.alter_column(
            "provider_api",
            existing_type=sa.String(length=255),
            nullable=True,
        )
    # Restore any quarantined rows so a re-upgrade has them available.
    op.execute(
        """
        INSERT INTO machines
        SELECT * FROM machines_quarantine_provider_api_null
        """
    )
    op.execute("DROP TABLE IF EXISTS machines_quarantine_provider_api_null")
