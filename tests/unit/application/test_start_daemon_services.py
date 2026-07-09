"""Unit tests for Application.start_daemon_services lifecycle behaviour."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal thin-fake Application that avoids heavy DI / file-system bootstrap
# ---------------------------------------------------------------------------


class _FakeApplication:
    """Thin substitute for :class:`orb.bootstrap.Application`.

    Exposes only the state and logic exercised by ``start_daemon_services``
    so tests run without the full DI container.
    """

    def __init__(
        self, *, initialized: bool = False, provider_instances: list | None = None
    ) -> None:
        self._initialized = initialized
        self._provider_instances = provider_instances or []

        self.logger = MagicMock()

        # Fake config manager returns a fake provider config.
        fake_provider_config = MagicMock()
        fake_provider_config.get_active_providers.return_value = list(self._provider_instances)
        self._config_manager = MagicMock()
        self._config_manager.get_provider_config.return_value = fake_provider_config

        # Fake provider registry returns strategy by name.
        self._strategy_by_name: dict[str, Any] = {}
        self._provider_registry = MagicMock()
        self._provider_registry.get_or_create_strategy.side_effect = lambda name, **_kw: (
            self._strategy_by_name.get(name)
        )

    def register_strategy(self, name: str, strategy: Any) -> None:
        self._strategy_by_name[name] = strategy

    # Copied verbatim from orb.bootstrap.Application.start_daemon_services
    # so that the tests exercise the real logic in isolation.
    async def start_daemon_services(self) -> bool:
        if not self._initialized:
            self.logger.warning(
                "start_daemon_services called before Application.initialize succeeded; skipping."
            )
            return False
        try:
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                return True
            ok = True
            for provider_instance in provider_config.get_active_providers():
                strategy = self._provider_registry.get_or_create_strategy(provider_instance.name)
                if strategy is None:
                    self.logger.warning(
                        "start_daemon_services: no strategy resolved for %s",
                        provider_instance.name,
                    )
                    ok = False
                    continue
                try:
                    await strategy.start_daemon_services()
                    self.logger.info(
                        "Daemon services started for provider instance %s",
                        provider_instance.name,
                    )
                except Exception as exc:
                    self.logger.error(
                        "Failed to start daemon services for %s: %s",
                        provider_instance.name,
                        exc,
                        exc_info=True,
                    )
                    ok = False
            return ok
        except Exception as exc:
            self.logger.error("start_daemon_services failed: %s", exc, exc_info=True)
            return False


def _make_provider_instance(name: str) -> MagicMock:
    inst = MagicMock()
    inst.name = name
    return inst


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_false_when_called_before_initialize() -> None:
    """start_daemon_services returns False and logs a warning when the application
    has not yet completed initialize()."""
    app = _FakeApplication(initialized=False)
    result = await app.start_daemon_services()
    assert result is False
    warning_texts = [str(c) for c in app.logger.warning.call_args_list]
    assert any("before Application.initialize" in t for t in warning_texts)


@pytest.mark.asyncio
async def test_one_strategy_failure_does_not_abort_others() -> None:
    """When one provider strategy's start_daemon_services raises, the remaining
    strategies are still awaited and the method returns False overall."""
    inst_a = _make_provider_instance("prov-a")
    inst_b = _make_provider_instance("prov-b")
    app = _FakeApplication(initialized=True, provider_instances=[inst_a, inst_b])

    second_started = asyncio.Event()

    strategy_a = MagicMock()
    strategy_a.start_daemon_services = AsyncMock(side_effect=RuntimeError("prov-a exploded"))

    strategy_b = MagicMock()

    async def _b_start() -> None:
        second_started.set()

    strategy_b.start_daemon_services = AsyncMock(side_effect=_b_start)

    app.register_strategy("prov-a", strategy_a)
    app.register_strategy("prov-b", strategy_b)

    result = await app.start_daemon_services()

    assert result is False
    assert second_started.is_set(), "strategy_b.start_daemon_services was not awaited"


@pytest.mark.asyncio
async def test_returns_true_when_all_strategies_succeed() -> None:
    """start_daemon_services returns True when every registered strategy completes
    without raising."""
    inst_a = _make_provider_instance("prov-a")
    inst_b = _make_provider_instance("prov-b")
    app = _FakeApplication(initialized=True, provider_instances=[inst_a, inst_b])

    strategy_a = MagicMock()
    strategy_a.start_daemon_services = AsyncMock(return_value=None)
    strategy_b = MagicMock()
    strategy_b.start_daemon_services = AsyncMock(return_value=None)

    app.register_strategy("prov-a", strategy_a)
    app.register_strategy("prov-b", strategy_b)

    result = await app.start_daemon_services()

    assert result is True
    strategy_a.start_daemon_services.assert_awaited_once()
    strategy_b.start_daemon_services.assert_awaited_once()


@pytest.mark.asyncio
async def test_server_initialize_application_calls_start_daemon_services() -> None:
    """The REST server initialisation path invokes orb_app.start_daemon_services()
    after Application.initialize() completes successfully."""
    fake_app = MagicMock()
    fake_app.initialize = AsyncMock(return_value=True)
    fake_app.start_daemon_services = AsyncMock(return_value=True)

    fake_container = MagicMock()
    fake_config_manager = MagicMock()
    fake_config_manager._config_file = None
    fake_container.get.return_value = fake_config_manager

    with (
        patch("orb.bootstrap.Application", return_value=fake_app),
    ):
        from orb.interface.server_command_handlers import _initialize_application  # noqa: PLC0415

        await _initialize_application(fake_container)

    fake_app.start_daemon_services.assert_awaited_once()


@pytest.mark.asyncio
async def test_cli_command_handler_does_not_call_start_daemon_services() -> None:
    """A CLI command path (handle_request_machines) does NOT call start_daemon_services."""
    # We check the source code does not contain a direct invocation of
    # start_daemon_services.  This is a static/lint-style assertion that
    # the CLI handler is free of the daemon-start call.
    import inspect

    from orb.interface import request_command_handlers

    source = inspect.getsource(request_command_handlers)
    assert "start_daemon_services" not in source, (
        "CLI command handler request_command_handlers must not call start_daemon_services"
    )
