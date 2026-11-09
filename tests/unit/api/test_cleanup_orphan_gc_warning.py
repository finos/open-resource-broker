"""Pruning request history warns when a Kubernetes orphan GC deletes pods.

The ports are stubbed at the port boundary. The capability itself is covered
against the real configuration chain in the k8s provider tests.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from orb.application.services.admin.cleanup_database import CleanupDatabaseService
from orb.sdk.client import ORBClient
from tests.unit.api.test_cleanup_router import (
    _make_config_port,
    _make_container,
    _make_machine,
    _make_request,
    _make_uow,
    _make_uow_factory,
    _patch_containers,
    cleanup_app,  # noqa: F401  # fixture
)

FEATURE = "deletes_pods_without_request_record"


def _strategy(deletes: bool) -> MagicMock:
    strategy = MagicMock()
    strategy.get_capabilities.return_value = SimpleNamespace(features={FEATURE: deletes})
    return strategy


def _ports(instances, cached=None, registered=True, created=None):
    config = MagicMock()
    config.get_active_providers.return_value = instances
    registry = MagicMock()
    registry.get_strategy.side_effect = lambda name: (cached or {}).get(name)
    registry.is_provider_registered.return_value = registered
    registry.create_strategy_by_type.return_value = created
    return config, registry


def _instance(name="k8s-a", type_="k8s"):
    return SimpleNamespace(name=name, type=type_)


def _service(config, registry, logger, requests=None):
    uow = _make_uow(request_map=requests or {})
    return CleanupDatabaseService(
        uow_factory=_make_uow_factory(uow),
        configuration_port=config,
        provider_registry=registry,
        logger=logger,
    )


def _bulk(service):
    return service.bulk_cleanup(statuses=["cancelled"])


def test_one_warning_naming_the_instance_when_the_gc_deletes() -> None:
    logger = MagicMock()
    config, registry = _ports([_instance()], cached={"k8s-a": _strategy(True)})

    _bulk(_service(config, registry, logger))

    assert logger.warning.call_count == 1
    assert "k8s-a" in logger.warning.call_args.args[1]


def test_no_warning_when_the_gc_does_not_delete() -> None:
    logger = MagicMock()
    config, registry = _ports([_instance()], cached={"k8s-a": _strategy(False)})

    _bulk(_service(config, registry, logger))

    logger.warning.assert_not_called()


def test_no_provider_instance_builds_no_strategy_and_logs_nothing() -> None:
    logger = MagicMock()
    config, registry = _ports([])

    _bulk(_service(config, registry, logger))

    registry.create_strategy_by_type.assert_not_called()
    registry.get_or_create_strategy.assert_not_called()
    logger.warning.assert_not_called()
    logger.debug.assert_not_called()


def test_uncached_instance_builds_one_throwaway_strategy_and_caches_nothing() -> None:
    logger = MagicMock()
    created = _strategy(False)
    config, registry = _ports([_instance()], created=created)

    _bulk(_service(config, registry, logger))

    registry.create_strategy_by_type.assert_called_once()
    registry.get_or_create_strategy.assert_not_called()
    created.initialize.assert_not_called()
    created.start_daemon_services.assert_not_called()
    assert registry.get_strategy("k8s-a") is None
    logger.warning.assert_not_called()


def test_uncached_instance_of_an_unregistered_type_is_skipped() -> None:
    logger = MagicMock()
    config, registry = _ports([_instance()], registered=False)

    _bulk(_service(config, registry, logger))

    registry.create_strategy_by_type.assert_not_called()


def test_uncached_instance_warns_from_the_throwaway_strategy() -> None:
    logger = MagicMock()
    config, registry = _ports([_instance()], created=_strategy(True))

    _bulk(_service(config, registry, logger))

    assert logger.warning.call_count == 1


def test_a_failing_lookup_does_not_block_the_cleanup() -> None:
    logger = MagicMock()
    config, registry = _ports([_instance()])
    registry.get_strategy.side_effect = RuntimeError("boom")
    request = _make_request("req-1", "cancelled")

    result = _bulk(_service(config, registry, logger, {"req-1": request}))

    assert result.requests_deleted == 1
    logger.warning.assert_not_called()
    assert logger.debug.called


def test_machine_only_delete_does_not_warn() -> None:
    logger = MagicMock()
    config, registry = _ports([_instance()], cached={"k8s-a": _strategy(True)})
    uow = _make_uow(machine_map={"m-1": _make_machine("m-1")})
    service = CleanupDatabaseService(
        uow_factory=_make_uow_factory(uow),
        configuration_port=config,
        provider_registry=registry,
        logger=logger,
    )

    service.delete_machine("m-1")

    logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Every surface reaches the same DI-resolved service
# ---------------------------------------------------------------------------


def _warning_service(logger, request):
    config, registry = _ports([_instance()], cached={"k8s-a": _strategy(True)})
    uow = _make_uow(request_map={request.request_id: request})
    factory = _make_uow_factory(uow)
    service = CleanupDatabaseService(
        uow_factory=factory,
        configuration_port=config,
        provider_registry=registry,
        logger=logger,
    )
    return service, factory


def test_rest_bulk_cleanup_warns(cleanup_app) -> None:  # noqa: F811
    logger = MagicMock()
    service, factory = _warning_service(logger, _make_request("req-1", "cancelled"))
    container = _make_container(_make_config_port(), factory, service)

    with _patch_containers("orb.api.routers.admin.get_di_container", container=container):
        response = TestClient(cleanup_app, raise_server_exceptions=False).post(
            "/admin/database/cleanup",
            json={"confirm": "CLEANUP", "request_statuses": ["cancelled"]},
        )

    assert response.status_code == 200, response.text
    assert logger.warning.call_count == 1


def test_rest_purge_warns(cleanup_app) -> None:  # noqa: F811
    logger = MagicMock()
    service, factory = _warning_service(logger, _make_request("req-1", "cancelled"))
    container = _make_container(_make_config_port(), factory, service)

    with _patch_containers(
        "orb.api.routers.admin.get_di_container",
        "orb.api.routers.requests.get_di_container",
        container=container,
    ):
        response = TestClient(cleanup_app, raise_server_exceptions=False).post(
            "/requests/req-1/purge"
        )

    assert response.status_code == 200, response.text
    assert logger.warning.call_count == 1


@pytest.mark.parametrize("method", ["cleanup_old_requests", "cleanup_all_resources"])
def test_sdk_cleanup_warns(method) -> None:
    logger = MagicMock()
    service, _factory = _warning_service(logger, _make_request("req-1", "cancelled"))
    client = object.__new__(ORBClient)
    client._container = MagicMock()
    client._container.get.side_effect = lambda t: service

    asyncio.run(getattr(client, method)())

    assert logger.warning.call_count == 1
