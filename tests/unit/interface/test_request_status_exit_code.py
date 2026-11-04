"""`orb requests status` exits non-zero when a request ended in a problem state."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.application.services.orchestration.dtos import GetRequestStatusOutput
from orb.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.response_formatting_service import ResponseFormattingService


def _rfs() -> ResponseFormattingService:
    scheduler = MagicMock()
    scheduler.format_request_status_response.side_effect = lambda reqs: {"requests": reqs}
    scheduler.get_exit_code_for_status.side_effect = lambda status: (
        BaseSchedulerStrategy.get_exit_code_for_status(scheduler, status)
    )
    scheduler.get_status_poll_exit_code.side_effect = lambda status: (
        DefaultSchedulerStrategy.get_status_poll_exit_code(scheduler, status)
    )
    return ResponseFormattingService(scheduler)


def _hostfactory_rfs() -> ResponseFormattingService:
    scheduler = MagicMock()
    scheduler.format_request_status_response.side_effect = lambda reqs: {"requests": reqs}
    scheduler.get_status_poll_exit_code.side_effect = lambda status: (
        HostFactorySchedulerStrategy.get_status_poll_exit_code(scheduler, status)
    )
    return ResponseFormattingService(scheduler)


def _render(interface: Interface, *items: dict, rfs=None):
    entry = OPERATION_CATALOG["get_request_status"]
    return entry.renderer_for(interface)(
        rfs or _rfs(), GetRequestStatusOutput(requests=list(items))
    )


@pytest.mark.unit
class TestRequestStatusExitCode:
    @pytest.mark.parametrize("status", ["complete", "running", "pending"])
    def test_healthy_statuses_exit_zero(self, status):
        assert _render(Interface.CLI, {"request_id": "r1", "status": status}).exit_code == 0

    @pytest.mark.parametrize("status", ["failed", "cancelled", "timeout", "partial"])
    def test_problem_statuses_exit_non_zero(self, status):
        assert _render(Interface.CLI, {"request_id": "r1", "status": status}).exit_code == 1

    def test_any_failing_request_among_many_fails_the_command(self):
        response = _render(
            Interface.CLI,
            {"request_id": "r1", "status": "complete"},
            {"request_id": "r2", "status": "failed"},
        )
        assert response.exit_code == 1

    def test_lookup_error_exits_non_zero(self):
        assert _render(Interface.CLI, {"request_id": "r1", "error": "not found"}).exit_code == 1

    def test_body_is_unchanged(self):
        item = {"request_id": "r1", "status": "failed"}
        assert _render(Interface.CLI, item).data == {"requests": [item]}

    def test_rest_rendering_is_unaffected(self):
        assert _render(Interface.REST, {"request_id": "r1", "status": "failed"}).exit_code == 0

    @pytest.mark.parametrize("status", ["failed", "cancelled", "timeout", "partial", "complete"])
    def test_hostfactory_always_exits_zero(self, status):
        response = _render(
            Interface.CLI, {"request_id": "r1", "status": status}, rfs=_hostfactory_rfs()
        )
        assert response.exit_code == 0

    def test_hostfactory_exits_zero_for_unknown_request(self):
        response = _render(
            Interface.CLI, {"request_id": "r1", "error": "not found"}, rfs=_hostfactory_rfs()
        )
        assert response.exit_code == 0
