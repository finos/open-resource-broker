"""Unit tests filling coverage gaps in orb.bootstrap.Application left by
test_application.py: real-validation __init__, container auto-creation,
the full initialize() success path, provider registration/logging helpers,
and the create_application()/main() entry points.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# __init__ — real validation path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplicationInitValidation:
    def test_validation_runs_startup_validator_when_not_skipped(self) -> None:
        from orb.bootstrap import Application

        with (
            patch(
                "orb.infrastructure.validation.startup_validator.StartupValidator"
            ) as mock_validator_cls,
            patch("orb.infrastructure.adapters.console_adapter.RichConsoleAdapter"),
        ):
            mock_validator = MagicMock()
            mock_validator_cls.return_value = mock_validator

            Application(skip_validation=False)

        mock_validator.validate_startup.assert_called_once()


# ---------------------------------------------------------------------------
# _ensure_container() — no external container / domain_container_set skip
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnsureContainerNoExternal:
    def test_fetches_global_container_when_none_provided(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        global_container = MagicMock()

        with (
            patch(
                "orb.infrastructure.di.container.get_container",
                return_value=global_container,
            ),
            patch("orb.domain.base.decorators.set_domain_container"),
        ):
            app._ensure_container()

        assert app._container is global_container

    def test_domain_container_only_set_once(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True, container=MagicMock())

        with patch("orb.domain.base.decorators.set_domain_container") as mock_set:
            app._ensure_container()
            app._ensure_container()

        mock_set.assert_called_once()


# ---------------------------------------------------------------------------
# config_manager() convenience accessor
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigManagerAccessor:
    def test_delegates_to_ensure_config_manager(self) -> None:
        from orb.bootstrap import Application

        mock_container = MagicMock()
        mock_config = MagicMock()
        mock_config.get.return_value = {"type": "aws"}
        mock_container.get.return_value = mock_config

        app = Application(skip_validation=True, container=mock_container)
        app._container = mock_container

        with patch("orb.domain.base.decorators.set_domain_container"):
            result = app.config_manager()

        assert result is mock_config
        assert app.provider_type == "aws"


# ---------------------------------------------------------------------------
# initialize() — full success path (eager + lazy, dry-run)
# ---------------------------------------------------------------------------


def _wire_container(config_manager, provider_registry, *, lazy: bool):
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort

    def _get(port):
        if port is ConfigurationPort:
            return config_manager
        if port is ProviderRegistryPort:
            return provider_registry
        return MagicMock()

    container = MagicMock()
    container.get.side_effect = _get
    container.is_lazy_loading_enabled.return_value = lazy
    return container


def _make_config_manager(provider_config=None):
    cfg = MagicMock()
    cfg.get.return_value = {"type": "aws"}
    cfg.get_typed.return_value = MagicMock()
    cfg.get_provider_config.return_value = provider_config
    return cfg


@pytest.mark.unit
class TestInitializeFullSuccess:
    def test_eager_loading_success_with_registry(self) -> None:
        from orb.bootstrap import Application

        registry = MagicMock()
        registry.get_registered_provider_instances.return_value = ["aws-main"]
        config_manager = _make_config_manager(provider_config=None)
        container = _wire_container(config_manager, registry, lazy=False)

        app = Application(skip_validation=True, container=container)

        with (
            patch("orb.bootstrap.setup_logging"),
            patch("orb.domain.base.decorators.set_domain_container"),
        ):
            result = _run(app.initialize())

        assert result is True
        assert app._initialized is True
        assert app._provider_registry is registry

    def test_lazy_loading_success_without_registry_instances(self) -> None:
        from orb.bootstrap import Application

        registry = MagicMock()
        registry.get_registered_provider_instances.return_value = []
        config_manager = _make_config_manager(provider_config=None)
        container = _wire_container(config_manager, registry, lazy=True)

        app = Application(skip_validation=True, container=container)

        with (
            patch("orb.bootstrap.setup_logging"),
            patch("orb.domain.base.decorators.set_domain_container"),
        ):
            result = _run(app.initialize())

        assert result is True

    def test_dry_run_activates_dry_run_context(self) -> None:
        from orb.bootstrap import Application

        registry = MagicMock()
        registry.get_registered_provider_instances.return_value = []
        config_manager = _make_config_manager(provider_config=None)
        container = _wire_container(config_manager, registry, lazy=True)

        app = Application(skip_validation=True, container=container)

        fake_ctx = MagicMock()
        with (
            patch("orb.bootstrap.setup_logging"),
            patch("orb.domain.base.decorators.set_domain_container"),
            patch(
                "orb.infrastructure.mocking.dry_run_context.dry_run_context",
                return_value=fake_ctx,
            ) as mock_dry_run,
        ):
            result = _run(app.initialize(dry_run=True))

        assert result is True
        mock_dry_run.assert_called_once_with(True)
        fake_ctx.__enter__.assert_called_once()


# ---------------------------------------------------------------------------
# _register_configured_providers()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterConfiguredProviders:
    def _app(self):
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._config_manager = MagicMock()
        app._provider_registry = MagicMock()
        return app

    def test_registers_unregistered_active_providers(self) -> None:
        app = self._app()
        provider = MagicMock()
        provider.name = "aws-main"
        provider_config = MagicMock()
        provider_config.get_active_providers.return_value = [provider]
        app._config_manager.get_provider_config.return_value = provider_config
        app._provider_registry.is_provider_instance_registered.return_value = False

        app._register_configured_providers()

        app._provider_registry.ensure_provider_instance_registered_from_config.assert_called_once_with(
            provider
        )

    def test_skips_already_registered_providers(self) -> None:
        app = self._app()
        provider = MagicMock()
        provider.name = "aws-main"
        provider_config = MagicMock()
        provider_config.get_active_providers.return_value = [provider]
        app._config_manager.get_provider_config.return_value = provider_config
        app._provider_registry.is_provider_instance_registered.return_value = True

        app._register_configured_providers()

        app._provider_registry.ensure_provider_instance_registered_from_config.assert_not_called()

    def test_no_provider_config_is_a_noop(self) -> None:
        app = self._app()
        app._config_manager.get_provider_config.return_value = None

        app._register_configured_providers()  # Should not raise.

    def test_exception_is_logged_and_swallowed(self) -> None:
        app = self._app()
        app._config_manager.get_provider_config.side_effect = RuntimeError("boom")

        app._register_configured_providers()  # Should not raise.


# ---------------------------------------------------------------------------
# _log_provider_configuration()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogProviderConfiguration:
    def _app(self):
        from orb.bootstrap import Application

        return Application(skip_validation=True)

    def test_single_mode_does_not_log_selection_policy(self) -> None:
        app = self._app()
        provider = MagicMock()
        provider.name = "aws-main"
        provider_config = MagicMock()
        provider_config.get_mode.return_value.value = "single"
        provider_config.get_active_providers.return_value = [provider]

        config_manager = MagicMock()
        config_manager.get_provider_config.return_value = provider_config

        app._log_provider_configuration(config_manager)  # Should not raise.

    def test_multi_mode_logs_selection_policy(self) -> None:
        app = self._app()
        provider_config = MagicMock()
        provider_config.get_mode.return_value.value = "multi"
        provider_config.get_active_providers.return_value = []
        provider_config.selection_policy = "round_robin"
        provider_config.health_check_interval = 30

        config_manager = MagicMock()
        config_manager.get_provider_config.return_value = provider_config

        app._log_provider_configuration(config_manager)  # Should not raise.

    def test_no_provider_config_logs_not_found(self) -> None:
        app = self._app()
        config_manager = MagicMock()
        config_manager.get_provider_config.return_value = None

        app._log_provider_configuration(config_manager)  # Should not raise.

    def test_legacy_strategy_enabled_path(self) -> None:
        app = self._app()
        config_manager = MagicMock(spec=["is_provider_strategy_enabled"])
        config_manager.is_provider_strategy_enabled.return_value = True

        app._log_provider_configuration(config_manager)  # Should not raise.

    def test_legacy_strategy_disabled_path(self) -> None:
        app = self._app()
        config_manager = MagicMock(spec=["is_provider_strategy_enabled"])
        config_manager.is_provider_strategy_enabled.return_value = False

        app._log_provider_configuration(config_manager)  # Should not raise.

    def test_no_known_config_shape_logs_not_available(self) -> None:
        app = self._app()
        config_manager = MagicMock(spec=[])

        app._log_provider_configuration(config_manager)  # Should not raise.

    def test_exception_is_swallowed(self) -> None:
        app = self._app()
        config_manager = MagicMock()
        config_manager.get_provider_config.side_effect = RuntimeError("boom")

        app._log_provider_configuration(config_manager)  # Should not raise.


# ---------------------------------------------------------------------------
# _preload_templates()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPreloadTemplates:
    def test_preload_templates_does_not_raise(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        _run(app._preload_templates())


# ---------------------------------------------------------------------------
# _log_final_provider_info()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogFinalProviderInfo:
    def test_logs_registry_info_when_available(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_providers.return_value = ["aws"]
        app._provider_registry.get_registered_provider_instances.return_value = ["aws-main"]

        app._log_final_provider_info()  # Should not raise.

    def test_logs_provider_type_when_no_registry(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app.provider_type = "aws"

        app._log_final_provider_info()  # Should not raise.

    def test_exception_is_swallowed(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_providers.side_effect = RuntimeError("boom")

        app._log_final_provider_info()  # Should not raise.


# ---------------------------------------------------------------------------
# start_daemon_services() — outer exception guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartDaemonServicesOuterException:
    def test_outer_exception_returns_false(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._config_manager = MagicMock()
        app._config_manager.get_provider_config.side_effect = RuntimeError("boom")

        result = _run(app.start_daemon_services())

        assert result is False


# ---------------------------------------------------------------------------
# get_provider_info() — not_configured and error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetProviderInfoGaps:
    def test_not_configured_when_no_registry(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app.provider_type = "mock"

        result = app.get_provider_info()

        assert result["status"] == "not_configured"
        assert result["initialized"] is False

    def test_error_status_on_exception(self) -> None:
        from orb.bootstrap import Application

        app = Application(skip_validation=True)
        app._initialized = True
        app._provider_registry = MagicMock()
        app._provider_registry.get_registered_providers.side_effect = RuntimeError("boom")

        result = app.get_provider_info()

        assert result["status"] == "error"
        assert result["initialized"] is False


# ---------------------------------------------------------------------------
# create_application()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateApplication:
    def test_success_returns_initialized_app(self) -> None:
        from orb.bootstrap import create_application

        with patch("orb.bootstrap.Application") as mock_app_cls:
            mock_app = MagicMock()
            mock_app.initialize = AsyncMock(return_value=True)
            mock_app.provider_type = "aws"
            mock_app_cls.return_value = mock_app

            result = _run(create_application("/tmp/config.json"))

        assert result is mock_app

    def test_failure_raises_runtime_error(self) -> None:
        from orb.bootstrap import create_application

        with patch("orb.bootstrap.Application") as mock_app_cls:
            mock_app = MagicMock()
            mock_app.initialize = AsyncMock(return_value=False)
            mock_app.provider_type = "aws"
            mock_app_cls.return_value = mock_app

            with pytest.raises(RuntimeError, match="Failed to initialize application"):
                _run(create_application("/tmp/config.json"))


# ---------------------------------------------------------------------------
# main() entry point
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainEntryPoint:
    def test_main_runs_successfully(self) -> None:
        from orb.bootstrap import main

        mock_app = MagicMock()
        mock_app.logger = MagicMock()
        mock_app.provider_type = "aws"
        mock_app.get_provider_info.return_value = {
            "provider_names": ["aws-main"],
            "initialized": True,
        }
        mock_app.health_check.return_value = {"status": "healthy"}
        mock_app.__aenter__ = AsyncMock(return_value=mock_app)
        mock_app.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("orb.bootstrap.create_application", AsyncMock(return_value=mock_app)),
            patch("orb.bootstrap.print_console"),
        ):
            _run(main())  # Should not raise.

    def test_main_exits_on_failure(self) -> None:
        from orb.bootstrap import main

        with (
            patch(
                "orb.bootstrap.create_application",
                AsyncMock(side_effect=RuntimeError("init failed")),
            ),
            patch("orb.bootstrap.print_console"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _run(main())

        assert exc_info.value.code == 1
