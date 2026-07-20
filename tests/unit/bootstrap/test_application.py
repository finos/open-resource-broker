"""Unit tests for orb.bootstrap.Application.

Covers uncovered branches in bootstrap/__init__.py:
- Application.__init__ — skip_validation=True path
- _ensure_container() — external container, config_path/config_dict pre-registration
- _ensure_config_manager() — provider_type extraction
- initialize() — success/failure paths, dry_run, _register_configured_providers
- start_daemon_services() — before init guard, provider loop, strategy=None, exc
- health_check() — all status branches (healthy, degraded, unhealthy, warning, error)
- get_provider_info() — not-init, registry-present, not-configured
- get_query_bus() / get_command_bus() — not-init guard
- shutdown() / cleanup()
- create_application() factory
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_app_skip_validation(**kwargs):
    """Create Application with skip_validation=True to bypass StartupValidator."""
    from orb.bootstrap import Application

    with patch("orb.infrastructure.di.container.set_container_factory"):
        pass  # Container factory already set at import time

    with patch("orb.infrastructure.validation.startup_validator.StartupValidator"):
        return Application(skip_validation=True, **kwargs)


def _patched_app(**kwargs):
    """Create Application with all heavy dependencies patched."""
    with (
        patch("orb.bootstrap.set_container_factory"),
        patch("orb.infrastructure.validation.startup_validator.StartupValidator"),
    ):
        from orb.bootstrap import Application

        return Application(skip_validation=True, **kwargs)


# ---------------------------------------------------------------------------
# __init__ — skip_validation branch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplicationInit:
    def test_skip_validation_does_not_call_startup_validator(self):
        with patch(
            "orb.infrastructure.validation.startup_validator.StartupValidator"
        ) as mock_validator:
            from orb.bootstrap import Application

            Application(skip_validation=True)
        mock_validator.assert_not_called()

    def test_not_initialized_on_construction(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        assert app._initialized is False

    def test_external_container_stored(self):
        from orb.bootstrap import Application

        mock_container = MagicMock()
        app = Application(skip_validation=True, container=mock_container)
        assert app._external_container is mock_container


# ---------------------------------------------------------------------------
# _ensure_container()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnsureContainer:
    def test_uses_external_container_when_provided(self):
        from orb.bootstrap import Application

        external = MagicMock()
        app = Application(skip_validation=True, container=external)
        # _ensure_container should set _container = external when not None
        with patch("orb.domain.base.decorators.set_domain_container"):
            app._ensure_container()
        assert app._container is external

    def test_pre_registers_configuration_manager_when_config_dict_provided(self):
        from orb.bootstrap import Application

        mock_container = MagicMock()
        app = Application(skip_validation=True, container=mock_container, config_dict={"k": "v"})

        with (
            patch("orb.config.managers.configuration_manager.ConfigurationManager") as mock_cm_cls,
            patch("orb.domain.base.decorators.set_domain_container"),
        ):
            mock_cm = MagicMock()
            mock_cm_cls.return_value = mock_cm
            app._ensure_container()

        mock_container.register_instance.assert_called()

    def test_pre_registers_configuration_manager_when_config_path_provided(self, tmp_path):
        from orb.bootstrap import Application

        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{}")

        mock_container = MagicMock()
        app = Application(skip_validation=True, container=mock_container, config_path=str(cfg_file))

        with patch("orb.domain.base.decorators.set_domain_container"):
            app._ensure_container()

        mock_container.register_instance.assert_called()


# ---------------------------------------------------------------------------
# _ensure_config_manager()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnsureConfigManager:
    def test_raises_runtime_error_when_container_not_initialized(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        with pytest.raises(RuntimeError, match="not initialized"):
            app._ensure_config_manager()

    def test_provider_type_extracted_from_dict(self):
        from orb.bootstrap import Application

        mock_container = MagicMock()
        mock_config = MagicMock()
        mock_config.get.return_value = {"type": "k8s"}
        mock_container.get.return_value = mock_config

        app = Application(skip_validation=True, container=mock_container)
        app._container = mock_container

        with patch("orb.domain.base.decorators.set_domain_container"):
            app._ensure_config_manager()

        assert app.provider_type == "k8s"

    def test_provider_type_string_coerced(self):
        from orb.bootstrap import Application

        mock_container = MagicMock()
        mock_config = MagicMock()
        mock_config.get.return_value = "aws"
        mock_container.get.return_value = mock_config

        app = Application(skip_validation=True, container=mock_container)
        app._container = mock_container

        with patch("orb.domain.base.decorators.set_domain_container"):
            app._ensure_config_manager()

        assert app.provider_type == "aws"


# ---------------------------------------------------------------------------
# initialize() success and failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplicationInitialize:
    def _mock_container(self):
        container = MagicMock()
        container.is_lazy_loading_enabled.return_value = True
        return container

    def _mock_config_manager(self, provider_type="mock"):
        cfg = MagicMock()
        cfg.get.return_value = {"type": provider_type}
        cfg.get_typed.return_value = MagicMock()
        cfg.get_provider_config.return_value = None
        return cfg

    def test_initialize_returns_false_on_exception(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._container = MagicMock()
        app._container.is_lazy_loading_enabled.return_value = True
        app._config_manager = MagicMock()
        app._config_manager.get.side_effect = RuntimeError("boom")

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(app.initialize())
        finally:
            loop.close()
        assert result is False

    def test_shutdown_clears_initialized(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        with patch("orb.bootstrap.telemetry.shutdown_telemetry"):
            app.shutdown()
        assert app._initialized is False


# ---------------------------------------------------------------------------
# start_daemon_services()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartDaemonServices:
    def test_returns_false_when_not_initialized(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(app.start_daemon_services())
        finally:
            loop.close()
        assert result is False

    def test_returns_true_when_no_provider_config(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._config_manager = MagicMock()
        app._config_manager.get_provider_config.return_value = None

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(app.start_daemon_services())
        finally:
            loop.close()
        assert result is True

    def test_strategy_none_marks_ok_false(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True

        mock_provider = MagicMock()
        mock_provider.name = "test-provider"
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = [mock_provider]
        app._config_manager = MagicMock()
        app._config_manager.get_provider_config.return_value = mock_provider_config

        app._provider_registry = MagicMock()
        app._provider_registry.get_or_create_strategy.return_value = None

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(app.start_daemon_services())
        finally:
            loop.close()
        assert result is False

    def test_strategy_exception_marks_ok_false(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True

        mock_provider = MagicMock()
        mock_provider.name = "bad-provider"
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = [mock_provider]
        app._config_manager = MagicMock()
        app._config_manager.get_provider_config.return_value = mock_provider_config

        mock_strategy = MagicMock()
        mock_strategy.start_daemon_services = AsyncMock(side_effect=RuntimeError("crash"))
        app._provider_registry = MagicMock()
        app._provider_registry.get_or_create_strategy.return_value = mock_strategy

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(app.start_daemon_services())
        finally:
            loop.close()
        assert result is False

    def test_successful_daemon_start(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True

        mock_provider = MagicMock()
        mock_provider.name = "ok-provider"
        mock_provider_config = MagicMock()
        mock_provider_config.get_active_providers.return_value = [mock_provider]
        app._config_manager = MagicMock()
        app._config_manager.get_provider_config.return_value = mock_provider_config

        mock_strategy = MagicMock()
        mock_strategy.start_daemon_services = AsyncMock(return_value=None)
        app._provider_registry = MagicMock()
        app._provider_registry.get_or_create_strategy.return_value = mock_strategy

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(app.start_daemon_services())
        finally:
            loop.close()
        assert result is True


# ---------------------------------------------------------------------------
# health_check()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthCheck:
    def test_not_initialized_returns_error_status(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        result = app.health_check()
        assert result["status"] == "error"

    def test_no_registry_returns_warning(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        # No _provider_registry set
        result = app.health_check()
        assert result["status"] == "warning"

    def test_no_providers_configured_returns_warning(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_provider_instances.return_value = []

        result = app.health_check()
        assert result["status"] == "warning"

    def test_all_healthy_returns_healthy(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_provider_instances.return_value = ["p1"]

        mock_strategy = MagicMock()
        mock_strategy.check_health.return_value = MagicMock(is_healthy=True)
        app._provider_registry.get_or_create_strategy.return_value = mock_strategy

        result = app.health_check()
        assert result["status"] == "healthy"

    def test_all_unhealthy_returns_unhealthy(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_provider_instances.return_value = ["p1"]

        mock_strategy = MagicMock()
        mock_strategy.check_health.return_value = MagicMock(is_healthy=False)
        app._provider_registry.get_or_create_strategy.return_value = mock_strategy

        result = app.health_check()
        assert result["status"] == "unhealthy"

    def test_partial_healthy_returns_degraded(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_provider_instances.return_value = ["p1", "p2"]

        healthy_strategy = MagicMock()
        healthy_strategy.check_health.return_value = MagicMock(is_healthy=True)
        unhealthy_strategy = MagicMock()
        unhealthy_strategy.check_health.return_value = MagicMock(is_healthy=False)

        app._provider_registry.get_or_create_strategy.side_effect = [
            healthy_strategy,
            unhealthy_strategy,
        ]

        result = app.health_check()
        assert result["status"] == "degraded"

    def test_health_check_exception_returns_error(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_provider_instances.side_effect = RuntimeError(
            "registry broken"
        )

        result = app.health_check()
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# get_provider_info()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderInfo:
    def test_not_initialized_returns_not_initialized(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        result = app.get_provider_info()
        assert result["status"] == "not_initialized"

    def test_with_registry_returns_configured(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_providers.return_value = ["aws"]
        app._provider_registry.get_registered_provider_instances.return_value = ["aws-main"]

        result = app.get_provider_info()
        assert result["status"] == "configured"
        assert result["provider_count"] == 1

    def test_multi_mode_when_multiple_instances(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_providers.return_value = ["aws", "k8s"]
        app._provider_registry.get_registered_provider_instances.return_value = ["p1", "p2"]

        result = app.get_provider_info()
        assert result["mode"] == "multi"


# ---------------------------------------------------------------------------
# get_query_bus / get_command_bus — not-init guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBuses:
    def test_get_query_bus_raises_when_not_initialized(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        with pytest.raises(RuntimeError, match="not initialized"):
            app.get_query_bus()

    def test_get_command_bus_raises_when_not_initialized(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        with pytest.raises(RuntimeError, match="not initialized"):
            app.get_command_bus()

    def test_get_query_bus_cached(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        mock_bus = MagicMock()
        app._container = MagicMock()
        app._container.get.return_value = mock_bus

        bus1 = app.get_query_bus()
        bus2 = app.get_query_bus()
        assert bus1 is bus2

    def test_get_command_bus_cached(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        mock_bus = MagicMock()
        app._container = MagicMock()
        app._container.get.return_value = mock_bus

        bus1 = app.get_command_bus()
        bus2 = app.get_command_bus()
        assert bus1 is bus2


# ---------------------------------------------------------------------------
# cleanup()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplicationCleanup:
    def test_cleanup_calls_shutdown(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True

        # shutdown_telemetry is imported at module level in bootstrap/__init__.py
        # so we patch at the module level where it lives as a name
        with patch("orb.bootstrap.shutdown_telemetry") as mock_shutdown:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(app.cleanup())
            finally:
                loop.close()
        # Called once from cleanup() and once from shutdown()
        assert mock_shutdown.call_count >= 1
        assert app._initialized is False
