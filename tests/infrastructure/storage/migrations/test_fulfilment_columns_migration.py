"""Migration + serializer round-trip tests for the fulfilment columns."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType
from orb.infrastructure.storage.repositories.request_repository import RequestSerializer

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "orb"
    / "infrastructure"
    / "storage"
    / "sql"
    / "migrations"
)

_NEW_COLUMNS = ["deadline_at", "partial_since", "last_transition_at", "fulfilment_diagnostic"]


def _alembic_config(db_url: str):
    from alembic.config import Config

    cfg = Config(str(_MIGRATIONS_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def _columns(engine: sa.Engine, table: str) -> set[str]:
    insp = sa.inspect(engine)
    return {c["name"] for c in insp.get_columns(table)}


def test_migration_upgrade_adds_columns(tmp_path):
    from alembic import command

    db_file = tmp_path / "orb.db"
    url = f"sqlite:///{db_file}"
    cfg = _alembic_config(url)

    command.upgrade(cfg, "head")

    engine = sa.create_engine(url)
    cols = _columns(engine, "requests")
    for col in _NEW_COLUMNS:
        assert col in cols, f"{col} missing after upgrade"
    engine.dispose()


def test_migration_downgrade_removes_columns(tmp_path):
    from alembic import command

    db_file = tmp_path / "orb.db"
    url = f"sqlite:///{db_file}"
    cfg = _alembic_config(url)

    command.upgrade(cfg, "head")
    # Downgrade to the parent revision (drops only the fulfilment columns).
    command.downgrade(cfg, "f6d2ba73f23c")

    engine = sa.create_engine(url)
    cols = _columns(engine, "requests")
    for col in _NEW_COLUMNS:
        assert col not in cols, f"{col} still present after downgrade"
    engine.dispose()


def test_serializer_round_trips_fulfilment_fields():
    """RequestSerializer persists and restores the four fulfilment fields."""
    serializer = RequestSerializer()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    diag = FulfilmentDiagnostic(
        category=DiagnosticCategory.CAPACITY,
        summary="insufficient capacity",
        occurred_at=now,
    )
    request = Request.create_new_request(RequestType.ACQUIRE, "tmpl", 3, "aws").model_copy(
        update={
            "status": RequestStatus.PARTIAL_PENDING,
            "deadline_at": now,
            "partial_since": now,
            "last_transition_at": now,
            "fulfilment_diagnostic": diag,
        }
    )

    data = serializer.to_dict(request)
    assert data["deadline_at"] is not None
    assert data["partial_since"] is not None
    assert data["last_transition_at"] is not None
    assert data["fulfilment_diagnostic"] is not None

    restored = serializer.from_dict(data)
    assert restored.status == RequestStatus.PARTIAL_PENDING
    assert restored.deadline_at == now
    assert restored.partial_since == now
    assert restored.last_transition_at == now
    assert restored.fulfilment_diagnostic is not None
    assert restored.fulfilment_diagnostic.category == DiagnosticCategory.CAPACITY


def test_serializer_legacy_row_missing_fields():
    """Legacy rows without the fulfilment columns load with None values."""
    serializer = RequestSerializer()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    legacy = {
        "request_id": "req-00000000-0000-0000-0000-000000000001",
        "template_id": "tmpl",
        "request_type": "acquire",
        "status": "in_progress",
        "provider_type": "aws",
        "requested_count": 1,
        "created_at": now.isoformat(),
        "version": 0,
    }
    restored = serializer.from_dict(legacy)
    assert restored.deadline_at is None
    assert restored.partial_since is None
    assert restored.last_transition_at is None
    assert restored.fulfilment_diagnostic is None


def test_serializer_diagnostic_from_dict_shape():
    """fulfilment_diagnostic arriving as a parsed dict (SQL path) round-trips."""
    serializer = RequestSerializer()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    data = {
        "request_id": "req-00000000-0000-0000-0000-000000000002",
        "template_id": "tmpl",
        "request_type": "acquire",
        "status": "partial",
        "provider_type": "aws",
        "requested_count": 1,
        "created_at": now.isoformat(),
        "version": 0,
        "fulfilment_diagnostic": {
            "category": "auth",
            "summary": "denied",
            "detail": None,
            "provider_errors": [],
            "occurred_at": now.isoformat(),
        },
    }
    restored = serializer.from_dict(data)
    assert restored.fulfilment_diagnostic is not None
    assert restored.fulfilment_diagnostic.category == DiagnosticCategory.AUTH
