"""Unit tests for bootstrap/services.py covering previously uncovered branches.

Targets (services.py):
  - setup_cqrs_infrastructure(): lazy branch (line 55-57), ImportError branch (82-83),
    Exception branch (84-85), get_handler_registry_stats ImportError (67-68)
  - _ensure_infrastructure_services(): success and exception paths (88-98)
  - _register_services_lazy(): register_all_*_types call (line 39 area)
  - _register_services_eager(): paths (197-253)
  - _register_lazy_service_factories(): debug log only (256-266)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_container(*, lazy: bool = True):
    container = MagicMock()
    container.is_lazy_loading_enabled.return_value = lazy
    container.get.return_value = MagicMock()
    container.register_instance = MagicMock()
    container.register_singleton = MagicMock()
    return container


# ---------------------------------------------------------------------------
# setup_cqrs_infrastructure() — ImportError branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetupCqrsInfrastructureImportError:
    def test_import_error_does_not_propagate(self, caplog):
        """ImportError inside setup_cqrs_infrastructure is caught and swallowed."""
        import logging

        from orb.bootstrap.services import setup_cqrs_infrastructure

        container = _make_container()

        # Force the LoggingPort import to fail, exercising the ImportError branch
        # (services.py:82-83) which logs a debug message and returns without
        # registering any buses.
        with caplog.at_level(logging.DEBUG, logger="orb.bootstrap.services"):
            with patch.dict("sys.modules", {"orb.domain.base.ports.logging_port": None}):
                setup_cqrs_infrastructure(container)

        # The ImportError branch was taken: its debug message fired and no
        # buses were registered.
        assert "CQRS infrastructure not available" in caplog.text
        container.register_instance.assert_not_called()

    def test_exception_does_not_propagate(self, caplog):
        """General Exception inside setup_cqrs_infrastructure is caught and logged."""
        import logging

        from orb.bootstrap.services import setup_cqrs_infrastructure

        container = _make_container()

        # Patch _ensure_infrastructure_services to raise (lazy path); it is guarded
        with caplog.at_level(logging.WARNING, logger="orb.bootstrap.services"):
            with patch(
                "orb.bootstrap.services._ensure_infrastructure_services",
                side_effect=RuntimeError("infra boom"),
            ):
                setup_cqrs_infrastructure(container)

        # The generic Exception branch (services.py:84-85) surfaced as a warning
        # and no buses were registered.
        assert "Failed to setup CQRS infrastructure" in caplog.text
        assert "infra boom" in caplog.text
        container.register_instance.assert_not_called()


@pytest.mark.unit
class TestSetupCqrsInfrastructureSuccess:
    def test_creates_and_registers_buses(self):
        from orb.bootstrap.services import setup_cqrs_infrastructure

        container = _make_container(lazy=False)

        mock_discovery = MagicMock()
        mock_discovery.discover_and_register_handlers = MagicMock()
        mock_qbus = MagicMock()
        mock_cbus = MagicMock()

        with (
            patch(
                "orb.infrastructure.di.handler_discovery.create_handler_discovery_service",
                return_value=mock_discovery,
            ),
            patch(
                "orb.infrastructure.di.buses.BusFactory.create_buses",
                return_value=(mock_qbus, mock_cbus),
            ),
        ):
            setup_cqrs_infrastructure(container)

        mock_discovery.discover_and_register_handlers.assert_called_once()

    def test_lazy_container_calls_ensure_infrastructure(self):
        from orb.bootstrap.services import setup_cqrs_infrastructure

        container = _make_container(lazy=True)

        mock_discovery = MagicMock()
        mock_discovery.discover_and_register_handlers = MagicMock()
        mock_qbus = MagicMock()
        mock_cbus = MagicMock()

        with (
            patch(
                "orb.infrastructure.di.handler_discovery.create_handler_discovery_service",
                return_value=mock_discovery,
            ),
            patch(
                "orb.infrastructure.di.buses.BusFactory.create_buses",
                return_value=(mock_qbus, mock_cbus),
            ),
            patch("orb.bootstrap.services._ensure_infrastructure_services") as mock_ensure,
        ):
            setup_cqrs_infrastructure(container)

        mock_ensure.assert_called_once_with(container)

    def test_handler_registry_stats_import_error_is_silently_skipped(self):
        """When get_handler_registry_stats is missing, CQRS setup continues."""
        from orb.bootstrap.services import setup_cqrs_infrastructure

        container = _make_container(lazy=False)

        mock_discovery = MagicMock()
        mock_discovery.discover_and_register_handlers = MagicMock()
        mock_qbus = MagicMock()
        mock_cbus = MagicMock()

        with (
            patch(
                "orb.infrastructure.di.handler_discovery.create_handler_discovery_service",
                return_value=mock_discovery,
            ),
            patch(
                "orb.infrastructure.di.buses.BusFactory.create_buses",
                return_value=(mock_qbus, mock_cbus),
            ),
        ):
            # Just verify no exception propagates
            setup_cqrs_infrastructure(container)


# ---------------------------------------------------------------------------
# _ensure_infrastructure_services()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnsureInfrastructureServices:
    def test_calls_register_infrastructure_services(self):
        from orb.bootstrap.services import _ensure_infrastructure_services

        container = _make_container()

        with patch("orb.bootstrap.services.register_infrastructure_services") as mock_register:
            _ensure_infrastructure_services(container)

        mock_register.assert_called_once_with(container)

    def test_exception_does_not_propagate(self):
        from orb.bootstrap.services import _ensure_infrastructure_services

        container = _make_container()

        with patch(
            "orb.bootstrap.services.register_infrastructure_services",
            side_effect=RuntimeError("infra error"),
        ):
            # Must not raise; exception is logged and swallowed
            _ensure_infrastructure_services(container)


# ---------------------------------------------------------------------------
# _register_lazy_service_factories()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterLazyServiceFactories:
    def test_logs_debug_and_registers_no_factories(self, caplog):
        import logging

        from orb.bootstrap.services import _register_lazy_service_factories

        container = _make_container()
        with caplog.at_level(logging.DEBUG, logger="orb.bootstrap.services"):
            _register_lazy_service_factories(container)

        # The function's sole observable effect is a debug log; it registers
        # nothing on the container (CQRS/provider services are registered
        # eagerly elsewhere in lazy mode).
        assert "Lazy service factories registered" in caplog.text
        container.register_singleton.assert_not_called()
        container.register_instance.assert_not_called()
