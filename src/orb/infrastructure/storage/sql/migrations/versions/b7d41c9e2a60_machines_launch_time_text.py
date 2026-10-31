"""store machines.launch_time as text

The ``launch_time`` column was declared BIGINT but the machine serializer writes
the launch time as an ISO-8601 string. SQLite accepts that (it stores whatever
it is given), PostgreSQL and MySQL reject it, so a machine with a launch time
could not be saved there. The column is now TEXT like every other timestamp
column. Existing epoch values (seconds or milliseconds, negative ones too) are
rewritten as ISO-8601 UTC text in the form the application writes
(``2026-10-20T12:00:00Z``), so that text order is chronological order across old
and new rows. A value that is neither an epoch nor ISO-8601 text stops the
migration before anything is changed.

Revision ID: b7d41c9e2a60
Revises: 55e3b5553d98
Create Date: 2026-11-01

"""

import re
from datetime import datetime, timezone
from typing import Sequence

import sqlalchemy as sa
from alembic import op

# Alembic reads these module globals by name (see the earlier migrations).
revision: str = "b7d41c9e2a60"
down_revision: str | Sequence[str] | None = "55e3b5553d98"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INTEGER = re.compile(r"-?[0-9]{1,18}")
_BATCH = 1000
# Epoch seconds stay below 1e11 until the year 5138; larger magnitudes are milliseconds
_MILLISECONDS_FROM = 10**11


def _parse_iso(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def _epoch_seconds(text: str) -> int | None:
    """Epoch seconds of an integer value (milliseconds are recognised by size)."""
    if not _INTEGER.fullmatch(text):
        return None
    number = int(text)
    return number // 1000 if abs(number) >= _MILLISECONDS_FROM else number


def _upgraded(value: object) -> str | None:
    """The value as ISO-8601 UTC text (no fraction, ``Z``); None if it is not an epoch/ISO value."""
    text = str(value).strip()
    seconds = _epoch_seconds(text)
    if seconds is None:
        return text if _parse_iso(text) is not None else None
    try:
        moment = datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _downgraded(value: object) -> str | None:
    """The value as epoch seconds (text of an integer); None if it is not an epoch/ISO value."""
    text = str(value).strip()
    seconds = _epoch_seconds(text)
    if seconds is not None:
        return str(seconds)
    moment = _parse_iso(text)
    return None if moment is None else str(int(moment.timestamp()))


def _plan(convert, direction: str) -> list[dict[str, object]]:  # type: ignore[no-untyped-def]
    """The updates that rewrite every launch_time through ``convert``.

    Nothing is changed here. A value ``convert`` cannot handle stops the migration
    with the number of such rows and the first id, so no data is dropped or left in
    a form that sorts wrongly.
    """
    rows = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT machine_id, launch_time FROM machines"
                " WHERE launch_time IS NOT NULL ORDER BY machine_id"
            )
        )
        .all()
    )
    updates: list[dict[str, object]] = []
    bad: list[object] = []
    for machine_id, value in rows:
        converted = convert(value)
        if converted is None:
            bad.append(machine_id)
        elif converted != str(value):
            updates.append({"id": machine_id, "value": converted})
    if bad:
        raise RuntimeError(
            f"Cannot {direction} machines.launch_time: {len(bad)} row(s) hold a value that is "
            f"neither an epoch timestamp nor ISO-8601 text (first machine_id: {bad[0]!r}). "
            "Correct or clear those values and run the migration again."
        )
    return updates


def _apply(updates: list[dict[str, object]]) -> None:
    bind = op.get_bind()
    update = sa.text("UPDATE machines SET launch_time = :value WHERE machine_id = :id")
    for start in range(0, len(updates), _BATCH):
        bind.execute(update, updates[start : start + _BATCH])


def upgrade() -> None:
    """Change launch_time from BIGINT to TEXT and rewrite epoch values as ISO-8601."""
    updates = _plan(_upgraded, "upgrade")
    with op.batch_alter_table("machines") as batch:
        batch.alter_column(
            "launch_time",
            existing_type=sa.BIGINT(),
            type_=sa.Text(),
            existing_nullable=True,
            postgresql_using="launch_time::text",
        )
    _apply(updates)


def downgrade() -> None:
    """Change launch_time back to BIGINT.

    ISO-8601 values become epoch seconds (a fraction of a second is dropped). A
    value that is neither stops the downgrade before anything is changed.
    """
    updates = _plan(_downgraded, "downgrade")
    _apply(updates)
    with op.batch_alter_table("machines") as batch:
        batch.alter_column(
            "launch_time",
            existing_type=sa.Text(),
            type_=sa.BIGINT(),
            existing_nullable=True,
            postgresql_using="launch_time::bigint",
        )
