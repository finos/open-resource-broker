"""Unit tests for RepositoryFactory and UnitOfWorkFactory.

Coverage targets: lines 51,55-59,61-63,77-79,83,87-89,91,93,95-97,107-109,124
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.infrastructure.utilities.factories.repository_factory import (
    RepositoryFactory,
    UnitOfWorkFactory,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config_manager(storage_type: str = "in_memory") -> MagicMock:
    cfg = MagicMock()
    cfg.get_storage_strategy.return_value = storage_type
    cfg.app_config.model_dump.return_value = {"storage": {"type": storage_type}}
    return cfg


def _make_logger() -> MagicMock:
    m = MagicMock()
    m.error = MagicMock()
    return m


def _make_factory(**kwargs) -> RepositoryFactory:
    defaults = {
        "config_manager": _make_config_manager(),
        "logger": _make_logger(),
    }
    defaults.update(kwargs)
    return RepositoryFactory(**defaults)


# ---------------------------------------------------------------------------
# storage_registry property (lazy load)
# ---------------------------------------------------------------------------


class TestStorageRegistryProperty:
    def test_storage_registry_is_loaded_lazily(self):
        factory = _make_factory()
        mock_registry = MagicMock()
        with patch(
            "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
            return_value=mock_registry,
        ):
            reg = factory.storage_registry
        assert reg is mock_registry

    def test_storage_registry_is_cached_after_first_access(self):
        factory = _make_factory()
        mock_registry = MagicMock()
        with patch(
            "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
            return_value=mock_registry,
        ) as mock_get:
            _ = factory.storage_registry
            _ = factory.storage_registry
        # Called once only
        mock_get.assert_called_once()


# ---------------------------------------------------------------------------
# create_machine_repository
# ---------------------------------------------------------------------------


class TestCreateMachineRepository:
    def test_creates_machine_repository_with_strategy(self):
        factory = _make_factory()
        mock_repo = MagicMock()
        mock_strategy = MagicMock()
        mock_registry = MagicMock()
        mock_registry.create_strategy.return_value = mock_strategy

        with (
            patch(
                "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.infrastructure.storage.repositories.machine_repository.MachineRepositoryImpl",
                return_value=mock_repo,
            ),
        ):
            result = factory.create_machine_repository()

        mock_registry.create_strategy.assert_called_once()
        assert result is mock_repo

    def test_logs_and_reraises_on_exception(self):
        factory = _make_factory()
        mock_registry = MagicMock()
        mock_registry.create_strategy.side_effect = RuntimeError("storage unavailable")

        with (
            patch(
                "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
                return_value=mock_registry,
            ),
            pytest.raises(RuntimeError, match="storage unavailable"),
        ):
            factory.create_machine_repository()

        factory.logger.error.assert_called()


# ---------------------------------------------------------------------------
# create_request_repository
# ---------------------------------------------------------------------------


class TestCreateRequestRepository:
    def test_creates_request_repository_with_strategy(self):
        factory = _make_factory()
        mock_repo = MagicMock()
        mock_strategy = MagicMock()
        mock_registry = MagicMock()
        mock_registry.create_strategy.return_value = mock_strategy

        with (
            patch(
                "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.infrastructure.storage.repositories.request_repository.RequestRepositoryImpl",
                return_value=mock_repo,
            ),
        ):
            result = factory.create_request_repository()

        assert result is mock_repo

    def test_logs_and_reraises_on_exception(self):
        factory = _make_factory()
        mock_registry = MagicMock()
        mock_registry.create_strategy.side_effect = ValueError("bad config")

        with (
            patch(
                "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
                return_value=mock_registry,
            ),
            pytest.raises(ValueError),
        ):
            factory.create_request_repository()

        factory.logger.error.assert_called()


# ---------------------------------------------------------------------------
# create_template_repository
# ---------------------------------------------------------------------------


class TestCreateTemplateRepository:
    def test_creates_template_repository_with_strategy(self):
        factory = _make_factory()
        mock_repo = MagicMock()
        mock_strategy = MagicMock()
        mock_registry = MagicMock()
        mock_registry.create_strategy.return_value = mock_strategy

        with (
            patch(
                "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.infrastructure.storage.repositories.template_repository.TemplateRepositoryImpl",
                return_value=mock_repo,
            ),
        ):
            result = factory.create_template_repository()

        assert result is mock_repo

    def test_logs_and_reraises_on_exception(self):
        factory = _make_factory()
        mock_registry = MagicMock()
        mock_registry.create_strategy.return_value = MagicMock()

        with (
            patch(
                "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
                return_value=mock_registry,
            ),
            patch(
                "orb.infrastructure.storage.repositories.template_repository.TemplateRepositoryImpl",
                side_effect=RuntimeError("template storage error"),
            ),
            pytest.raises(RuntimeError),
        ):
            factory.create_template_repository()

        factory.logger.error.assert_called()


# ---------------------------------------------------------------------------
# create_unit_of_work
# ---------------------------------------------------------------------------


class TestCreateUnitOfWork:
    def test_creates_unit_of_work_via_registry(self):
        factory = _make_factory()
        mock_uow = MagicMock()
        mock_registry = MagicMock()
        mock_registry.create_unit_of_work.return_value = mock_uow

        with patch(
            "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
            return_value=mock_registry,
        ):
            result = factory.create_unit_of_work()

        assert result is mock_uow

    def test_logs_and_reraises_on_exception(self):
        factory = _make_factory()
        mock_registry = MagicMock()
        mock_registry.create_unit_of_work.side_effect = RuntimeError("uow error")

        with (
            patch(
                "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
                return_value=mock_registry,
            ),
            pytest.raises(RuntimeError),
        ):
            factory.create_unit_of_work()

        factory.logger.error.assert_called()


# ---------------------------------------------------------------------------
# UnitOfWorkFactory
# ---------------------------------------------------------------------------


class TestUnitOfWorkFactory:
    def test_create_delegates_to_repository_factory(self):
        cfg = _make_config_manager()
        logger = _make_logger()
        uow_factory = UnitOfWorkFactory(config_manager=cfg, logger=logger)
        mock_uow = MagicMock()
        mock_registry = MagicMock()
        mock_registry.create_unit_of_work.return_value = mock_uow

        with patch(
            "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
            return_value=mock_registry,
        ):
            result = uow_factory.create()

        assert result is mock_uow

    def test_create_unit_of_work_delegates_to_create(self):
        cfg = _make_config_manager()
        logger = _make_logger()
        uow_factory = UnitOfWorkFactory(config_manager=cfg, logger=logger)
        mock_uow = MagicMock()
        mock_registry = MagicMock()
        mock_registry.create_unit_of_work.return_value = mock_uow

        with patch(
            "orb.infrastructure.utilities.factories.repository_factory.get_storage_registry",
            return_value=mock_registry,
        ):
            result = uow_factory.create_unit_of_work()

        assert result is mock_uow

    def test_repository_factory_property_creates_new_instance(self):
        cfg = _make_config_manager()
        logger = _make_logger()
        uow_factory = UnitOfWorkFactory(config_manager=cfg, logger=logger)
        rf = uow_factory.repository_factory
        assert isinstance(rf, RepositoryFactory)
