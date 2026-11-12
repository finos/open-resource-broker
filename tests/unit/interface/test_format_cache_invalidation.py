"""Scheduler overrides and configuration reloads drop cached format strategies.

A header-selected format is built once by the strategy factory and cached. These tests
prove the override sites invalidate it before the container's formatter is resolved,
and that a reload makes the next header-selected call build a fresh strategy.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.dependencies import CurrentUser, get_current_user
from orb.api.routers.admin import router as admin_router
from orb.config.managers.configuration_manager import ConfigurationManager
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory
from orb.infrastructure.scheduler.registry import get_scheduler_registry
from orb.interface.format_selection import select_formatter
from orb.interface.response_formatting_service import ResponseFormattingService
from tests.utilities.reset_singletons import reset_all_singletons


class _Container:
    def __init__(self, log: list[str] | None = None) -> None:
        self.log = log if log is not None else []
        config = MagicMock()
        config.get_scheduler_strategy.return_value = "default"
        config.override_scheduler_strategy.side_effect = lambda s: self.log.append(f"override:{s}")
        factory = SchedulerStrategyFactory()
        real_invalidate = factory.invalidate
        factory.invalidate = lambda: (self.log.append("invalidate"), real_invalidate())[1]  # type: ignore[method-assign]
        configured = ResponseFormattingService(self._strategy("default", 0))
        self.services = {
            ConfigurationPort: config,
            ConfigurationManager: MagicMock(),
            SchedulerStrategyFactory: factory,
            ResponseFormattingService: configured,
        }

    @staticmethod
    def _strategy(name: str, build: int) -> MagicMock:
        strategy = MagicMock()
        strategy.get_scheduler_type.return_value = name
        strategy.format_machine_status_response.return_value = {"build": build}
        return strategy

    def get(self, key):
        self.log.append(f"get:{getattr(key, '__name__', key)}")
        return self.services[key]


@pytest.fixture
def probe_container():
    """A container plus a format ``reload-probe`` whose output changes with each build."""
    container = _Container()
    builds = {"count": 0}

    def build(_config):
        builds["count"] += 1
        return _Container._strategy("reload-probe", builds["count"])

    get_scheduler_registry().register(
        "reload-probe", build, lambda c: None, strategy_class=MagicMock
    )
    yield container
    reset_all_singletons()


def _output(container) -> int:
    formatter = select_formatter(container, "reload-probe")
    return formatter.format_machine_list([]).data["build"]


def test_without_a_reload_the_header_selected_output_is_cached(probe_container):
    assert _output(probe_container) == _output(probe_container) == 1


def test_admin_reload_changes_header_selected_output(probe_container):
    first = _output(probe_container)
    app = FastAPI()
    app.include_router(admin_router)
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(username="a", role="admin")

    with patch("orb.api.routers.admin.get_di_container", return_value=probe_container):
        response = TestClient(app).post("/admin/reload-config")

    assert response.status_code == 200, response.text
    probe_container.services[ConfigurationManager].reload.assert_called_once()
    assert (first, _output(probe_container)) == (1, 2)


def test_sighup_reload_changes_header_selected_output(probe_container):
    from orb.interface.server_runtime import _reload_config_from_signal

    first = _output(probe_container)

    _reload_config_from_signal(
        MagicMock(),
        probe_container.services[ConfigurationManager],
        probe_container.services[SchedulerStrategyFactory],
    )

    assert (first, _output(probe_container)) == (1, 2)


def test_failed_reload_keeps_the_cached_format(probe_container):
    from orb.interface.server_runtime import _reload_config_from_signal

    first = _output(probe_container)
    cm = probe_container.services[ConfigurationManager]
    cm.reload.side_effect = ValueError("bad config")

    _reload_config_from_signal(MagicMock(), cm, probe_container.services[SchedulerStrategyFactory])

    assert (first, _output(probe_container)) == (1, 1)


def test_server_scheduler_override_invalidates_before_the_formatter_is_resolved():
    from orb.interface.server_command_handlers import _resolve_configs

    container = _Container()
    container.services[ConfigurationManager].get_typed_with_defaults.side_effect = lambda t: t()
    args = argparse.Namespace(
        _container=container,
        host=None,
        port=None,
        workers=None,
        server_log_level=None,
        scheduler="hostfactory",
    )

    _resolve_configs(args)

    assert container.log.index("override:hostfactory") < container.log.index("invalidate")
    assert "get:ResponseFormattingService" not in container.log
