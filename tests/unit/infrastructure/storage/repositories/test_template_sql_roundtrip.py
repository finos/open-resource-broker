"""SQL round-trip tests for TemplateRepositoryImpl against a real SQLite backend.

These tests exercise the *actual* SQL write path — ``TemplateSerializer.to_dict``
-> ``SQLQueryBuilder.build_insert`` (which filters incoming data against the ORM
column allowlist) -> a physical SQLite table created from the ORM models. They
guard the storage-layer translation seam: the domain aggregate exposes
``machine_*`` fields while the physical columns keep their original names
(``image_id``, ``max_instances``, ``root_device_volume_size``, ``volume_type``,
``key_name``, ``user_data``, ``instance_profile``).

If ``to_dict`` emitted the domain field names instead of the physical column
names, the allowlist filter in ``build_insert`` would silently drop every one of
those values on INSERT — persisted templates would lose their image, SSH key,
bootstrap, disk sizing, machine role, and reset the max cap to the server
default. These tests fail loudly if that regression returns.
"""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orb.domain.template.template_aggregate import Template
from orb.infrastructure.storage.repositories.template_repository import (
    TemplateRepositoryImpl,
    TemplateSerializer,
)
from orb.infrastructure.storage.sql.models import TemplateModel
from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

# The six fields renamed to the machine_* scheme plus machine_role, each paired
# with the physical DB column the value must land in.
_ATTR_TO_COLUMN = {
    "machine_image": "image_id",
    "max_machines": "max_instances",
    "machine_disk_size_gb": "root_device_volume_size",
    "machine_disk_type": "volume_type",
    "machine_ssh_key": "key_name",
    "machine_bootstrap": "user_data",
    "machine_role": "instance_profile",
}

# A fully-populated set of non-default values for every renamed field.
_POPULATED = {
    "machine_image": "ami-0123456789abcdef0",
    "max_machines": 42,
    "machine_disk_size_gb": 250,
    "machine_disk_type": "gp3",
    "machine_ssh_key": "prod-ssh-key",
    "machine_bootstrap": "#!/bin/bash\necho bootstrap",
    "machine_role": "arn:aws:iam::123456789012:instance-profile/orb",
}


def _sql_columns() -> dict[str, str]:
    """Derive the SQLQueryBuilder column dict from the ORM model, mirroring
    the production SQLUnitOfWork wiring (keys are ``col.key`` = physical name)."""
    return {
        col.key: ("TEXT PRIMARY KEY" if col.primary_key else "TEXT")
        for col in TemplateModel.__table__.columns  # type: ignore[attr-defined]
    }


def _make_template() -> Template:
    return Template.model_validate(
        {
            "template_id": "tpl-roundtrip",
            "name": "Round Trip",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "provider_api": "RunInstances",
            "created_at": _NOW,
            "updated_at": _NOW,
            "machine_type": "t3.medium",
            **_POPULATED,
        }
    )


@pytest.fixture()
def sqlite_db_path():
    """A temp-file SQLite DB so state survives across strategy instances."""
    fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    fd.close()
    path = Path(fd.name)
    yield str(path)
    path.unlink(missing_ok=True)


@pytest.mark.unit
class TestTemplateSqlRoundTrip:
    """Full save->reload through the real SQL backend preserves every field."""

    def _make_strategy(self, db_path: str) -> SQLStorageStrategy:
        return SQLStorageStrategy(
            config={"type": "sqlite", "name": db_path},
            table_name="templates",
            columns=_sql_columns(),
        )

    def test_renamed_fields_survive_sql_insert(self, sqlite_db_path):
        """Saving a fully-populated Template persists every renamed field to its
        physical column instead of silently dropping it at the allowlist."""
        template = _make_template()
        strategy = self._make_strategy(sqlite_db_path)
        repo = TemplateRepositoryImpl(strategy)

        repo.save(template)

        raw = strategy.find_by_id("tpl-roundtrip")
        assert raw is not None, "row was not persisted at all"

        for attr, column in _ATTR_TO_COLUMN.items():
            expected = getattr(template, attr)
            assert raw.get(column) == expected, (
                f"physical column {column!r} lost value from domain field "
                f"{attr!r}: expected {expected!r}, got {raw.get(column)!r}"
            )

    def test_max_machines_not_reset_to_server_default(self, sqlite_db_path):
        """The max cap must persist as-provided, not fall back to the column's
        server_default of 1."""
        template = _make_template()
        strategy = self._make_strategy(sqlite_db_path)
        TemplateRepositoryImpl(strategy).save(template)

        raw = strategy.find_by_id("tpl-roundtrip")
        assert raw is not None
        assert raw["max_instances"] == 42

    def test_from_dict_reconstructs_machine_fields_from_physical_columns(self, sqlite_db_path):
        """The read path rebuilds the machine_* domain fields from the physical
        column names written by to_dict."""
        template = _make_template()
        strategy = self._make_strategy(sqlite_db_path)
        TemplateRepositoryImpl(strategy).save(template)

        raw = strategy.find_by_id("tpl-roundtrip")
        assert raw is not None

        # ``SELECT *`` always returns the ``instance_type`` column (NULL here),
        # which ``_normalize_machine_types`` interprets as a single machine type.
        # That machine_types/instance_type interaction is orthogonal to the
        # renamed-field seam under test, so drop the empty key before rebuilding.
        raw.pop("instance_type", None)
        reconstructed = TemplateSerializer().from_dict(raw)

        for attr in _ATTR_TO_COLUMN:
            assert getattr(reconstructed, attr) == getattr(template, attr), (
                f"machine_* field {attr!r} was not reconstructed from its physical column on read"
            )
