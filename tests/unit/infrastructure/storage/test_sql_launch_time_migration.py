"""machines.launch_time is TEXT in the model and in the migration chain."""

from __future__ import annotations

import os
from pathlib import Path

import alembic.command
import alembic.config
import pytest
from sqlalchemy import create_engine, inspect, text

import orb
from orb.infrastructure.storage.sql.models import MachineModel

PREVIOUS_HEAD = "55e3b5553d98"
INI = os.path.join(
    os.path.dirname(os.path.abspath(orb.__file__)),
    "infrastructure",
    "storage",
    "sql",
    "migrations",
    "alembic.ini",
)


@pytest.fixture
def keep_logging_config():
    """alembic's env.py reconfigures logging and disables existing loggers."""
    import logging

    loggers = [
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]
    state = [(logger, logger.disabled, logger.level, logger.propagate) for logger in loggers]
    root_level, root_handlers = logging.root.level, list(logging.root.handlers)
    yield
    for logger, disabled, level, propagate in state:
        logger.disabled, logger.level, logger.propagate = disabled, level, propagate
    logging.root.setLevel(root_level)
    logging.root.handlers[:] = root_handlers


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "orb.db"
    monkeypatch.setenv("ORB_SQL_URL", f"sqlite:///{path}")
    return path


def _upgrade(revision: str) -> None:
    alembic.command.upgrade(alembic.config.Config(INI), revision)


def _downgrade(revision: str) -> None:
    alembic.command.downgrade(alembic.config.Config(INI), revision)


def _launch_time_type(path: Path) -> str:
    engine = create_engine(f"sqlite:///{path}")
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("machines")}
        return str(columns["launch_time"]["type"]).upper()
    finally:
        engine.dispose()


def _insert(path: Path, machine_id: str, launch_time: object) -> None:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO machines (machine_id, instance_type, provider_api, provider_name,"
                    " template_id, image_id, version, launch_time)"
                    " VALUES (:id, 't3', 'RunInstances', 'aws', 't', 'ami-1', 0, :lt)"
                ),
                {"id": machine_id, "lt": launch_time},
            )
    finally:
        engine.dispose()


def _launch_times(path: Path) -> dict[str, object]:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT machine_id, launch_time FROM machines")).all()
        return {machine_id: value for machine_id, value in rows}
    finally:
        engine.dispose()


@pytest.mark.unit
def test_model_declares_text() -> None:
    assert str(MachineModel.__table__.c.launch_time.type).upper() == "TEXT"  # type: ignore[attr-defined]


@pytest.mark.unit
def test_migration_changes_the_column_and_rewrites_epoch_values(
    database: Path, keep_logging_config: None
) -> None:
    _upgrade(PREVIOUS_HEAD)
    assert _launch_time_type(database) == "BIGINT"
    _insert(database, "i-iso", "2026-03-01T12:00:00Z")
    _insert(database, "i-epoch", 1772366400)
    _insert(database, "i-none", None)

    _upgrade("head")

    assert _launch_time_type(database) == "TEXT"
    values = _launch_times(database)
    assert values["i-iso"] == "2026-03-01T12:00:00Z"
    assert values["i-epoch"] == "2026-03-01T12:00:00Z"
    assert values["i-none"] is None


@pytest.mark.unit
def test_migrated_database_accepts_iso_text_through_the_orm(
    database: Path, keep_logging_config: None
) -> None:
    _upgrade("head")

    _insert(database, "i-1", "2026-03-01T12:00:00.000005Z")

    assert _launch_times(database) == {"i-1": "2026-03-01T12:00:00.000005Z"}


@pytest.mark.unit
def test_downgrade_restores_bigint(database: Path, keep_logging_config: None) -> None:
    _upgrade("head")

    _downgrade(PREVIOUS_HEAD)

    assert _launch_time_type(database) == "BIGINT"


def _page_ids(path: Path, *, descending: bool, after=None, limit: int = 50):  # type: ignore[no-untyped-def]
    from orb.infrastructure.storage.sql.registration import create_sql_entity_strategy

    config = {"storage": {"strategy": "sql", "sql_strategy": {"type": "sqlite", "name": str(path)}}}
    strategy = create_sql_entity_strategy(config, "machines")
    page = strategy.find_page({}, "launch_time", descending, after, limit)
    assert page is not None
    rows, has_more, _ = page
    return [(row["machine_id"], key) for row, key in rows], has_more


def _mixed_database(path: Path) -> None:
    """Epoch rows written by the old schema, ISO rows written after the upgrade."""
    _upgrade(PREVIOUS_HEAD)
    _insert(path, "old-oct05", 1791158400)  # 2026-10-05T00:00:00Z
    _insert(path, "old-oct20", 1792454400)  # 2026-10-20T00:00:00Z
    _insert(path, "old-none", None)
    _upgrade("head")
    _insert(path, "new-oct12", "2026-10-12T08:30:00Z")
    _insert(path, "new-oct25", "2026-10-25T00:00:00.000125Z")


@pytest.mark.unit
def test_epoch_rows_become_iso_text_in_the_written_format(
    database: Path, keep_logging_config: None
) -> None:
    _mixed_database(database)

    values = _launch_times(database)

    assert values["old-oct05"] == "2026-10-05T00:00:00Z"
    assert values["old-oct20"] == "2026-10-20T00:00:00Z"
    assert values["old-none"] is None


@pytest.mark.unit
@pytest.mark.parametrize("descending", [False, True])
def test_sort_and_cursor_pages_by_launch_time_mix_old_and_new_rows_chronologically(
    database: Path, keep_logging_config: None, descending: bool
) -> None:
    _mixed_database(database)
    chronological = ["old-none", "old-oct05", "new-oct12", "old-oct20", "new-oct25"]
    expected = list(reversed(chronological)) if descending else chronological

    everything, has_more = _page_ids(database, descending=descending)
    assert [machine_id for machine_id, _ in everything] == expected
    assert not has_more

    walked: list[str] = []
    after = None
    while True:
        page, has_more = _page_ids(database, descending=descending, after=after, limit=2)
        walked += [machine_id for machine_id, _ in page[:2]]
        if not has_more:
            break
        last_id, last_key = page[1]
        after = (last_key, last_id)
    assert walked == expected


@pytest.mark.unit
def test_downgrade_then_upgrade_round_trips_launch_times(
    database: Path, keep_logging_config: None
) -> None:
    _mixed_database(database)

    _downgrade(PREVIOUS_HEAD)
    downgraded = _launch_times(database)
    assert downgraded["old-oct05"] == 1791158400
    assert downgraded["new-oct12"] == 1791793800
    assert downgraded["new-oct25"] == 1792886400  # the fraction of a second is dropped
    assert downgraded["old-none"] is None

    _upgrade("head")
    assert _launch_times(database)["old-oct20"] == "2026-10-20T00:00:00Z"
    assert _launch_times(database)["new-oct12"] == "2026-10-12T08:30:00Z"


@pytest.mark.unit
def test_machine_reads_a_leftover_epoch_string() -> None:
    from orb.infrastructure.storage.repositories.machine_repository import MachineSerializer

    machine = MachineSerializer().from_dict(
        {
            "machine_id": "i-1",
            "instance_type": "t3",
            "provider_api": "RunInstances",
            "provider_name": "aws",
            "template_id": "t",
            "image_id": "ami-1",
            "status": "running",
            "launch_time": "1792454400",
        }
    )

    assert machine.launch_time is not None
    assert machine.launch_time.isoformat() == "2026-10-20T00:00:00+00:00"


@pytest.mark.unit
def test_millisecond_and_negative_epochs_become_iso_text(
    database: Path, keep_logging_config: None
) -> None:
    _upgrade(PREVIOUS_HEAD)
    _insert(database, "ms", 1792454400123)  # 2026-10-20T00:00:00.123Z
    _insert(database, "neg", -5)
    _insert(database, "iso", "2026-10-12T08:30:00Z")

    _upgrade("head")

    assert _launch_times(database) == {
        "ms": "2026-10-20T00:00:00Z",
        "neg": "1969-12-31T23:59:55Z",
        "iso": "2026-10-12T08:30:00Z",
    }


@pytest.mark.unit
def test_upgrade_with_an_unparsable_value_changes_nothing_and_names_the_row(
    database: Path, keep_logging_config: None
) -> None:
    _upgrade(PREVIOUS_HEAD)
    _insert(database, "a-good", 1792454400)
    _insert(database, "b-garbage", "not a time")
    _insert(database, "c-garbage", "also not")

    with pytest.raises(RuntimeError, match=r"2 row\(s\).*'b-garbage'"):
        _upgrade("head")

    assert _launch_time_type(database) == "BIGINT"
    assert _launch_times(database)["a-good"] == 1792454400


@pytest.mark.unit
def test_downgrade_with_an_unparsable_value_changes_nothing_and_names_the_row(
    database: Path, keep_logging_config: None
) -> None:
    _upgrade("head")
    _insert(database, "a-good", "2026-10-20T00:00:00Z")
    _insert(database, "b-garbage", "garbage")

    with pytest.raises(RuntimeError, match=r"1 row\(s\).*'b-garbage'"):
        _downgrade(PREVIOUS_HEAD)

    assert _launch_time_type(database) == "TEXT"
    assert _launch_times(database)["a-good"] == "2026-10-20T00:00:00Z"
