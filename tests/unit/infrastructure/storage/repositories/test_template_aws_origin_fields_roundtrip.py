"""Persisted round trip for the template fields that originate on AWS.

``machine_types_ondemand``, ``machine_types_priority``, ``iops`` and
``throughput`` live on the shared template aggregate. The repository
serialises through an explicit key list, so a field missing from that list is
dropped silently on save. These tests load -> save -> load a template with all
four populated through each storage backend and assert the values survive.

The pool-split fields (``machine_types_ondemand`` / ``machine_types_priority``)
do not survive today: ``TemplateSerializer.from_dict`` reads only the
``vmTypesOnDemand`` / ``vmTypesPriority`` spellings while ``to_dict`` writes the
snake_case names, so the stored values are lost on load. Those assertions are
strict expected failures; whoever fixes the read path must remove the marker.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orb.domain.template.template_aggregate import Template
from orb.infrastructure.storage.json.strategy import JSONStorageStrategy
from orb.infrastructure.storage.repositories.template_repository import (
    TemplateRepositoryImpl,
    TemplateSerializer,
)
from orb.infrastructure.storage.sql.models import TemplateModel
from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_AWS_ORIGIN = {
    "machine_types_ondemand": {"m5.large": 2},
    "machine_types_priority": {"c5.large": 3},
    "iops": 4000,
    "throughput": 250,
}


def _template() -> Template:
    return Template.model_validate(
        {
            "template_id": "tpl-aws-origin",
            "name": "AWS origin fields",
            "provider_type": "aws",
            "provider_name": "aws-us-east-1",
            "provider_api": "EC2Fleet",
            "machine_image": "ami-0123456789abcdef0",
            "machine_types": {"m5.large": 1, "c5.large": 1},
            "subnet_ids": ["subnet-1"],
            "security_group_ids": ["sg-1"],
            "created_at": _NOW,
            "updated_at": _NOW,
            **_AWS_ORIGIN,
        }
    )


_POOL_SPLIT_DROPPED = pytest.mark.xfail(
    strict=True,
    reason="from_dict reads vmTypesOnDemand/vmTypesPriority, not the keys to_dict writes",
)


def _assert_scalar_fields(template: Template) -> None:
    assert template.iops == _AWS_ORIGIN["iops"]
    assert template.throughput == _AWS_ORIGIN["throughput"]
    assert template.subnet_ids == ["subnet-1"]
    assert template.security_group_ids == ["sg-1"]


def _assert_pool_split(template: Template) -> None:
    assert template.machine_types_ondemand == _AWS_ORIGIN["machine_types_ondemand"]
    assert template.machine_types_priority == _AWS_ORIGIN["machine_types_priority"]


def _serializer_round_trip() -> Template:
    serializer = TemplateSerializer()
    first = serializer.from_dict(serializer.to_dict(_template()))
    return serializer.from_dict(serializer.to_dict(first))


def _json_round_trip(tmp_path: Path) -> Template:
    path = str(tmp_path / "templates.json")

    def repo() -> TemplateRepositoryImpl:
        return TemplateRepositoryImpl(JSONStorageStrategy(path, entity_type="templates"))

    repo().save(_template())
    loaded = repo().find_by_template_id("tpl-aws-origin")
    assert loaded is not None
    repo().save(loaded)
    again = repo().find_by_template_id("tpl-aws-origin")
    assert again is not None
    return again


@pytest.fixture()
def sqlite_db_path() -> Iterator[str]:
    fd = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    fd.close()
    path = Path(fd.name)
    yield str(path)
    path.unlink(missing_ok=True)


def _sql_round_trip(db_path: str) -> Template:
    columns = {
        col.key: ("TEXT PRIMARY KEY" if col.primary_key else "TEXT")
        for col in TemplateModel.__table__.columns  # type: ignore[attr-defined]
    }

    def strategy() -> SQLStorageStrategy:
        return SQLStorageStrategy(
            config={"type": "sqlite", "name": db_path}, table_name="templates", columns=columns
        )

    TemplateRepositoryImpl(strategy()).save(_template())
    raw = strategy().find_by_id("tpl-aws-origin")
    assert raw is not None
    # ``SELECT *`` returns an empty instance_type column that would be read as
    # a single machine type; it is unrelated to the fields under test.
    raw.pop("instance_type", None)
    return TemplateSerializer().from_dict(raw)


@pytest.mark.unit
def test_serializer_round_trip_keeps_scalar_fields() -> None:
    _assert_scalar_fields(_serializer_round_trip())


@_POOL_SPLIT_DROPPED
@pytest.mark.unit
def test_serializer_round_trip_keeps_pool_split() -> None:
    _assert_pool_split(_serializer_round_trip())


@pytest.mark.unit
def test_json_file_backend_keeps_scalar_fields(tmp_path: Path) -> None:
    _assert_scalar_fields(_json_round_trip(tmp_path))


@_POOL_SPLIT_DROPPED
@pytest.mark.unit
def test_json_file_backend_keeps_pool_split(tmp_path: Path) -> None:
    _assert_pool_split(_json_round_trip(tmp_path))


@pytest.mark.unit
def test_sql_backend_keeps_scalar_fields(sqlite_db_path: str) -> None:
    _assert_scalar_fields(_sql_round_trip(sqlite_db_path))


@_POOL_SPLIT_DROPPED
@pytest.mark.unit
def test_sql_backend_keeps_pool_split(sqlite_db_path: str) -> None:
    _assert_pool_split(_sql_round_trip(sqlite_db_path))
