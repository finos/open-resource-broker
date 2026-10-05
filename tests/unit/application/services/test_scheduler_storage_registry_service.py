"""Unit tests for SchedulerRegistryService and StorageRegistryService.

Both are thin delegation wrappers over a registry object; tests focus on
correct delegation and the error-swallowing fallback paths for health/
capability lookups.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.services.scheduler_registry_service import SchedulerRegistryService
from orb.application.services.storage_registry_service import StorageRegistryService


@pytest.mark.unit
class TestSchedulerRegistryService:
    def test_get_available_schedulers_delegates(self):
        registry = MagicMock()
        registry.get_registered_types.return_value = ["slurm", "k8s"]
        service = SchedulerRegistryService(registry=registry, logger=MagicMock())

        assert service.get_available_schedulers() == ["slurm", "k8s"]

    def test_create_scheduler_strategy_delegates(self):
        registry = MagicMock()
        registry.create_strategy.return_value = "strategy-instance"
        service = SchedulerRegistryService(registry=registry, logger=MagicMock())

        result = service.create_scheduler_strategy("slurm", {"option": 1})

        assert result == "strategy-instance"
        registry.create_strategy.assert_called_once_with("slurm", {"option": 1})

    def test_is_scheduler_registered_delegates(self):
        registry = MagicMock()
        registry.is_registered.return_value = True
        service = SchedulerRegistryService(registry=registry, logger=MagicMock())

        assert service.is_scheduler_registered("slurm") is True

    def test_get_scheduler_capabilities_returns_strategy_capabilities(self):
        registry = MagicMock()
        strategy = MagicMock()
        strategy.get_capabilities.return_value = {"supports_gpu": True}
        registry.create_strategy.return_value = strategy
        service = SchedulerRegistryService(registry=registry, logger=MagicMock())

        result = service.get_scheduler_capabilities("slurm")

        assert result == {"supports_gpu": True}

    def test_get_scheduler_capabilities_defaults_when_strategy_lacks_method(self):
        registry = MagicMock()
        strategy = MagicMock(spec=[])  # no get_capabilities attribute
        registry.create_strategy.return_value = strategy
        service = SchedulerRegistryService(registry=registry, logger=MagicMock())

        result = service.get_scheduler_capabilities("slurm")

        assert result == {}

    def test_get_scheduler_capabilities_returns_empty_dict_on_exception(self):
        registry = MagicMock()
        registry.create_strategy.side_effect = RuntimeError("unknown scheduler type")
        service = SchedulerRegistryService(registry=registry, logger=MagicMock())

        result = service.get_scheduler_capabilities("unknown")

        assert result == {}


@pytest.mark.unit
class TestStorageRegistryService:
    def test_get_available_storage_types_delegates(self):
        registry = MagicMock()
        registry.get_registered_types.return_value = ["dynamodb", "sql"]
        service = StorageRegistryService(registry=registry, logger=MagicMock())

        assert service.get_available_storage_types() == ["dynamodb", "sql"]

    def test_create_storage_strategy_delegates(self):
        registry = MagicMock()
        registry.create_strategy.return_value = "storage-strategy"
        service = StorageRegistryService(registry=registry, logger=MagicMock())

        result = service.create_storage_strategy("dynamodb", {"table": "requests"})

        assert result == "storage-strategy"
        registry.create_strategy.assert_called_once_with("dynamodb", {"table": "requests"})

    def test_is_storage_registered_delegates(self):
        registry = MagicMock()
        registry.is_registered.return_value = False
        service = StorageRegistryService(registry=registry, logger=MagicMock())

        assert service.is_storage_registered("dynamodb") is False

    def test_get_storage_health_returns_strategy_health(self):
        registry = MagicMock()
        strategy = MagicMock()
        strategy.check_health.return_value = {"status": "healthy"}
        registry.create_strategy.return_value = strategy
        service = StorageRegistryService(registry=registry, logger=MagicMock())

        result = service.get_storage_health("dynamodb")

        assert result == {"status": "healthy"}

    def test_get_storage_health_defaults_when_strategy_lacks_method(self):
        registry = MagicMock()
        strategy = MagicMock(spec=[])  # no check_health attribute
        registry.create_strategy.return_value = strategy
        service = StorageRegistryService(registry=registry, logger=MagicMock())

        result = service.get_storage_health("dynamodb")

        assert result == {"status": "unknown"}

    def test_get_storage_health_returns_error_status_on_exception(self):
        registry = MagicMock()
        registry.create_strategy.side_effect = RuntimeError("connection refused")
        service = StorageRegistryService(registry=registry, logger=MagicMock())

        result = service.get_storage_health("sql")

        assert result["status"] == "error"
        assert "connection refused" in result["message"]
