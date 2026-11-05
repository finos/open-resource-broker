"""Repositories built through the registry see the versioned-save capability.

Nothing between the repository and the strategy (instrumentation, adapters) may
hide it, or the repositories silently fall back to unconditional saves.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

moto = pytest.importorskip("moto")

from orb.infrastructure.storage.registry import get_storage_registry  # noqa: E402
from orb.infrastructure.storage.repositories.versioned_save import (  # noqa: E402
    supports_versioned_save,
)
from orb.infrastructure.utilities.factories.repository_factory import (  # noqa: E402
    RepositoryFactory,
)


def _factory(storage: str, app_config: dict) -> RepositoryFactory:
    manager = MagicMock()
    manager.get_storage_strategy.return_value = storage
    manager.app_config.model_dump.return_value = app_config
    return RepositoryFactory(config_manager=manager, logger=MagicMock(), event_bus=None)


@pytest.fixture(params=["json", "sql", "dynamodb"])
def factory(request, tmp_path):
    from orb.infrastructure.storage.json.registration import register_json_storage
    from orb.infrastructure.storage.sql.registration import register_sql_storage
    from orb.providers.aws.storage.registration import register_dynamodb_storage

    registry = get_storage_registry()
    if request.param == "json":
        register_json_storage()
        yield _factory("json", {})
    elif request.param == "sql":
        register_sql_storage()
        yield _factory(
            "sql",
            {"storage": {"sql_strategy": {"type": "sqlite", "name": str(tmp_path / "orb.db")}}},
        )
    else:
        # No profile: a runner has no AWS config file, only these credentials
        for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            request.getfixturevalue("monkeypatch").setenv(name, "testing")
        register_dynamodb_storage(registry)
        with moto.mock_aws():
            yield _factory(
                "dynamodb",
                {"region": "us-east-1", "profile": "", "table_prefix": "capability"},
            )


def test_repositories_see_the_capability_on_every_backend(factory):
    for repository in (
        factory.create_machine_repository(),
        factory.create_request_repository(),
    ):
        assert supports_versioned_save(repository.storage_strategy)


def test_the_unit_of_work_strategies_advertise_it_too(tmp_path):
    from orb.infrastructure.storage.json.unit_of_work import JSONUnitOfWork

    uow = JSONUnitOfWork(data_dir=str(tmp_path), backup_enabled=False)

    assert supports_versioned_save(uow.requests.storage_strategy)
    assert supports_versioned_save(uow.machines.storage_strategy)
