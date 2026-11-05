"""Aurora resolves one connection from configuration on every storage path."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.storage.exceptions import StorageError
from orb.infrastructure.storage.registry import StorageRegistry
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.storage.config import AuroraSqlStrategyConfig, AWSStorageConfig
from orb.providers.aws.storage.registration import (
    create_aurora_entity_strategy,
    create_aurora_strategy,
    create_aurora_unit_of_work,
    register_aurora_storage,
    resolve_aurora_connection,
)

PASSWORD = "p@ss/w:rd#1"
AURORA = {
    "cluster_endpoint": "orb.cluster-abc.eu-west-1.rds.amazonaws.com",
    "port": 3306,
    "name": "orb",
    "username": "orb_user",
    "password": PASSWORD,
    "pool_size": 7,
    "max_overflow": 3,
}


def _dumped(aurora: dict[str, Any] | None = AURORA, **provider: Any) -> dict[str, Any]:
    """The application config as RepositoryFactory dumps it."""
    storage = {"aurora": aurora} if aurora is not None else {}
    return {
        "storage": {"strategy": "aurora", "sql_strategy": {"type": "sqlite"}},
        "provider": {
            "active_provider": "aws-main",
            "providers": [
                {"name": "other", "type": "aws", "config": {"storage": {"aurora": {"host": "x"}}}},
                {
                    "name": "aws-main",
                    "type": "aws",
                    "enabled": True,
                    "config": {"region": "eu-west-1", "storage": storage},
                    **provider,
                },
            ],
        },
    }


def _manager(aurora: dict[str, Any] | None = AURORA) -> MagicMock:
    from orb.config.manager import ConfigurationManager

    storage = AWSStorageConfig(aurora=AuroraSqlStrategyConfig(**aurora) if aurora else None)  # type: ignore[call-arg]
    manager = MagicMock(spec=ConfigurationManager)
    manager.get_typed.return_value = AWSProviderConfig(region="eu-west-1", storage=storage)  # type: ignore[call-arg]
    return manager


class _AdapterLike:
    """The configuration adapter exposes get_typed(class) too."""

    def __init__(self, manager: MagicMock) -> None:
        self._manager = manager

    def get_typed(self, key: Any, _expected_type: Any = None, _default: Any = None) -> Any:
        return self._manager.get_typed(key)


def _sources() -> dict[str, Any]:
    return {
        "dumped-app-config": _dumped(),
        "configuration-manager": _manager(),
        "configuration-adapter": _AdapterLike(_manager()),
        "settings-object": AuroraSqlStrategyConfig(**AURORA),  # type: ignore[arg-type]
    }


@pytest.mark.unit
class TestResolution:
    @pytest.mark.parametrize("source", list(_sources()))
    def test_every_source_resolves_the_same_target(self, source: str) -> None:
        url, _ = resolve_aurora_connection(_sources()[source])

        assert url.drivername == "mysql+pymysql"
        assert url.host == AURORA["cluster_endpoint"]
        assert url.port == 3306
        assert url.database == "orb"
        assert url.username == "orb_user"
        assert url.password == PASSWORD

    def test_credentials_with_reserved_characters_survive_the_connection_string(self) -> None:
        from sqlalchemy.engine import make_url

        url, _ = resolve_aurora_connection(_dumped())
        rendered = url.render_as_string(hide_password=False)

        assert make_url(rendered).password == PASSWORD

    def test_plain_host_is_used_when_there_is_no_cluster_endpoint(self) -> None:
        aurora = {k: v for k, v in AURORA.items() if k != "cluster_endpoint"} | {"host": "db.local"}

        url, _ = resolve_aurora_connection(_dumped(aurora))

        assert url.host == "db.local"

    def test_ssl_options_become_connection_arguments(self) -> None:
        aurora = AURORA | {"ssl_ca": "/etc/ssl/rds.pem", "ssl_verify": False}

        url, _ = resolve_aurora_connection(_dumped(aurora))

        assert dict(url.query) == {"ssl_ca": "/etc/ssl/rds.pem", "ssl_verify_cert": "false"}

    def test_active_provider_wins_over_other_aws_instances(self) -> None:
        url, _ = resolve_aurora_connection(_dumped())

        assert url.host == AURORA["cluster_endpoint"]

    def test_explicit_connection_string_is_used_as_given(self) -> None:
        url, settings = resolve_aurora_connection({"connection_string": "sqlite:///x.db"})

        assert settings is None
        assert url.render_as_string() == "sqlite:///x.db"


@pytest.mark.unit
class TestMissingConfiguration:
    @pytest.mark.parametrize(
        "config",
        [{}, {"storage": {}}, _dumped(None), _manager(None), object()],
        ids=[
            "empty",
            "no-aurora-section",
            "dumped-without-aurora",
            "manager-without-aurora",
            "obj",
        ],
    )
    def test_missing_aurora_settings_are_an_error_not_a_localhost_default(
        self, config: Any
    ) -> None:
        with pytest.raises(StorageError):
            resolve_aurora_connection(config)

    @pytest.mark.parametrize("field", ["name", "username", "password"])
    def test_incomplete_settings_name_the_missing_field(self, field: str) -> None:
        aurora = {k: v for k, v in AURORA.items() if k != field}

        with pytest.raises(StorageError) as exc_info:
            resolve_aurora_connection(_dumped(aurora))

        assert field in exc_info.value.message
        assert PASSWORD not in str(exc_info.value.message)

    def test_invalid_settings_do_not_echo_the_password(self) -> None:
        aurora = AURORA | {"port": "not-a-port"}

        with pytest.raises(StorageError) as exc_info:
            resolve_aurora_connection(_dumped(aurora))

        assert "port" in exc_info.value.message
        assert PASSWORD not in repr(exc_info.value.__dict__)
        assert PASSWORD not in str(exc_info.value.message)


@pytest.fixture
def created_engines():
    """Record the engines built for Aurora (the MySQL driver is not needed)."""
    from orb.infrastructure.storage.components.engine_registry import dispose_engines

    dispose_engines()
    with patch("sqlalchemy.create_engine") as create_engine:
        create_engine.side_effect = lambda *a, **k: MagicMock(name="engine")
        yield create_engine
    dispose_engines()


@pytest.mark.unit
class TestEveryPathUsesTheResolvedConnection:
    def test_repository_path_with_the_dumped_application_config(self, created_engines) -> None:
        from orb.infrastructure.utilities.factories.repository_factory import RepositoryFactory

        registry_cls = type(f"_Registry{uuid.uuid4().hex}", (StorageRegistry,), {})
        registry = registry_cls()
        register_aurora_storage(registry)
        manager = MagicMock()
        manager.get_storage_strategy.return_value = "aurora"
        manager.app_config.model_dump.return_value = _dumped()
        factory = RepositoryFactory(manager, MagicMock())
        factory._storage_registry = registry

        captured: list[dict[str, Any]] = []
        with patch(
            "orb.infrastructure.storage.sql.entities.create_entity_strategy",
            side_effect=lambda cfg, table: captured.append(cfg) or MagicMock(table_name=table),
        ):
            factory.create_machine_repository()
            factory.create_request_repository()
            factory.create_template_repository()
        StorageRegistry._instances.pop(registry_cls.__name__, None)

        assert len(captured) == 3
        url = created_engines.call_args.args[0]
        assert url.host == AURORA["cluster_endpoint"]
        assert created_engines.call_count == 1
        assert all(cfg["engine"] is captured[0]["engine"] for cfg in captured)

    def test_health_probe_path(self, created_engines) -> None:
        with patch(
            "orb.infrastructure.storage.sql.entities.create_entity_strategy",
            side_effect=lambda cfg, table: MagicMock(),
        ):
            create_aurora_strategy(_dumped())

        assert created_engines.call_args.args[0].host == AURORA["cluster_endpoint"]

    def test_unit_of_work_path_applies_the_pool_settings(self, created_engines) -> None:
        with patch("orb.infrastructure.storage.sql.unit_of_work.SQLUnitOfWork") as uow:
            create_aurora_unit_of_work(_dumped())

        args, kwargs = created_engines.call_args
        assert args[0].host == AURORA["cluster_endpoint"]
        assert kwargs["pool_size"] == 7
        assert kwargs["max_overflow"] == 3
        uow.assert_called_once()

    @pytest.mark.parametrize(
        "build",
        [
            lambda c: create_aurora_entity_strategy(c, "requests"),
            create_aurora_strategy,
            create_aurora_unit_of_work,
        ],
        ids=["entity", "probe", "unit-of-work"],
    )
    def test_missing_configuration_fails_the_same_way_on_each_path(self, build: Any) -> None:
        with pytest.raises(StorageError) as exc_info:
            build(_dumped(None))

        assert "no connection could be derived" in exc_info.value.message
