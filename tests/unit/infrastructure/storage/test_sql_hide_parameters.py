"""SQL engines never put bound values into error text."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from orb.infrastructure.storage.components.sql_connection_manager import shared_engine_for_url


@pytest.mark.unit
def test_failed_statement_message_keeps_the_error_but_not_the_bound_values(
    tmp_path: Path,
) -> None:
    engine = shared_engine_for_url(f"sqlite:///{tmp_path / 'p.db'}")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE t (id TEXT PRIMARY KEY)"))
            conn.execute(text("INSERT INTO t (id) VALUES (:v)"), {"v": "sensitive-value-123"})
        with pytest.raises(IntegrityError) as caught:
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO t (id) VALUES (:v)"), {"v": "sensitive-value-123"})
    finally:
        engine.dispose()

    message = str(caught.value)
    assert "UNIQUE constraint failed" in message
    assert "sensitive-value-123" not in message


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, url: object, **kwargs: object) -> object:
        from unittest.mock import MagicMock

        self.calls.append(kwargs)
        return MagicMock()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch):
    import sqlalchemy

    from orb.infrastructure.storage.components.engine_registry import dispose_engines

    created = _Recorder()
    monkeypatch.setattr(sqlalchemy, "create_engine", created)
    yield created
    dispose_engines()


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    ["sqlite:///:memory:", "sqlite:///relative-test.db", "postgresql://u:p@db.example/orb"],
)
def test_configured_sql_engines_hide_parameters(recorder: _Recorder, url: str) -> None:
    from types import SimpleNamespace

    from sqlalchemy.engine import make_url

    from orb.infrastructure.storage.sql.registration import _new_engine

    config = SimpleNamespace(pool_size=2, max_overflow=1)
    _new_engine(make_url(url), config)

    assert recorder.calls and all(call["hide_parameters"] is True for call in recorder.calls)


@pytest.mark.unit
def test_aurora_engines_hide_parameters(recorder: _Recorder) -> None:
    from orb.providers.aws.storage.registration import _aurora_engine, _validated_aurora_settings

    settings = _validated_aurora_settings(
        {
            "cluster_endpoint": "orb.cluster-abc.eu-west-1.rds.amazonaws.com",
            "port": 3306,
            "name": "orb",
            "username": "orb_user",
            "password": "pw",
        }
    )
    from orb.providers.aws.storage.registration import _aurora_url

    _aurora_engine(_aurora_url(settings), settings)
    _aurora_engine(_aurora_url(settings), None)

    assert len(recorder.calls) >= 1
    assert all(call["hide_parameters"] is True for call in recorder.calls)


@pytest.mark.unit
def test_migration_engine_hides_parameters() -> None:
    from pathlib import Path as P

    import orb

    env = P(orb.__file__).parent / "infrastructure/storage/sql/migrations/env.py"
    assert "hide_parameters=True" in env.read_text()
