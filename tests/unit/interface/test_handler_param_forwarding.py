"""Tests for handler parameter forwarding — Task 1.

Verifies that CLI handlers correctly forward all relevant args to their
input DTOs instead of silently dropping them.
"""

from __future__ import annotations

import argparse
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _make_args(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _make_container(**orchestrators):
    from orb.infrastructure.di.buses import CommandBus, QueryBus
    from orb.interface.response_formatting_service import ResponseFormattingService

    container = MagicMock()
    formatter = MagicMock(spec=ResponseFormattingService)
    formatter.format_machine_list.return_value = {"machines": []}
    formatter.format_template_list.return_value = {"templates": []}
    formatter.format_template_mutation.return_value = {"success": True}
    formatter.format_request_status.return_value = {"requests": []}

    def _get(cls):
        name = cls.__name__
        if name in orchestrators:
            return orchestrators[name]
        if cls is ResponseFormattingService:
            return formatter
        if cls is CommandBus:
            return MagicMock()
        if cls is QueryBus:
            return MagicMock()
        return MagicMock()

    container.get.side_effect = _get
    return container


class TestHandleListMachinesForwardsParams:
    def test_handle_list_machines_forwards_limit_and_offset(self):
        from orb.application.services.orchestration.dtos import ListMachinesInput
        from orb.application.services.orchestration.list_machines import ListMachinesOrchestrator
        from orb.interface.machine_command_handlers import handle_list_machines

        mock_orch = MagicMock(spec=ListMachinesOrchestrator)
        mock_orch.execute = AsyncMock(return_value=MagicMock(machines=[]))
        container = _make_container(ListMachinesOrchestrator=mock_orch)

        args = _make_args(status=None, provider=None, request_id=None, limit=5, offset=10)

        with patch("orb.interface.machine_command_handlers.get_container", return_value=container):
            asyncio.run(handle_list_machines(args))

        mock_orch.execute.assert_called_once()
        call_input: ListMachinesInput = mock_orch.execute.call_args[0][0]
        assert call_input.limit == 5
        assert call_input.offset == 10


class TestHandleListTemplatesForwardsParams:
    def test_handle_list_templates_forwards_limit_offset_and_provider_api(self):
        from orb.application.services.orchestration.dtos import ListTemplatesInput
        from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
        from orb.interface.template_command_handlers import handle_list_templates

        mock_orch = MagicMock(spec=ListTemplatesOrchestrator)
        mock_orch.execute = AsyncMock(return_value=MagicMock(templates=["t1"]))
        container = _make_container(ListTemplatesOrchestrator=mock_orch)
        container.get.side_effect = lambda cls: (
            mock_orch if cls.__name__ == "ListTemplatesOrchestrator" else MagicMock()
        )

        args = _make_args(
            input_data=None,
            provider_api="EC2Fleet",
            provider_name=None,
            active_only=True,
            limit=5,
            offset=10,
        )

        with patch("orb.interface.template_command_handlers.get_container", return_value=container):
            asyncio.run(handle_list_templates(args))

        mock_orch.execute.assert_called_once()
        call_input: ListTemplatesInput = mock_orch.execute.call_args[0][0]
        assert call_input.limit == 5
        assert call_input.offset == 10
        assert call_input.provider_api == "EC2Fleet"
        assert call_input.active_only is True


class TestHandleGetReturnRequestsForwardsParams:
    def test_handle_get_return_requests_forwards_status_and_limit(self):
        from orb.application.services.orchestration.dtos import ListReturnRequestsInput
        from orb.application.services.orchestration.list_return_requests import (
            ListReturnRequestsOrchestrator,
        )
        from orb.interface.request_command_handlers import handle_get_return_requests

        mock_orch = MagicMock(spec=ListReturnRequestsOrchestrator)
        mock_orch.execute = AsyncMock(return_value=MagicMock(requests=[]))
        container = _make_container(ListReturnRequestsOrchestrator=mock_orch)

        args = _make_args(status="pending", limit=5)

        with patch("orb.interface.request_command_handlers.get_container", return_value=container):
            asyncio.run(handle_get_return_requests(args))

        mock_orch.execute.assert_called_once()
        call_input: ListReturnRequestsInput = mock_orch.execute.call_args[0][0]
        assert call_input.status == "pending"
        assert call_input.limit == 5


class TestHandleProviderHealthForwardsParams:
    def test_handle_provider_health_forwards_provider_name(self):
        from orb.application.services.orchestration.dtos import GetProviderHealthInput
        from orb.application.services.orchestration.get_provider_health import (
            GetProviderHealthOrchestrator,
        )
        from orb.interface.system_command_handlers import handle_provider_health

        mock_orch = MagicMock(spec=GetProviderHealthOrchestrator)
        mock_orch.execute = AsyncMock(return_value=MagicMock(health={}, message="ok"))
        container = _make_container(GetProviderHealthOrchestrator=mock_orch)

        args = _make_args(provider="aws")

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            asyncio.run(handle_provider_health(args))

        mock_orch.execute.assert_called_once()
        call_input: GetProviderHealthInput = mock_orch.execute.call_args[0][0]
        assert call_input.provider_name == "aws"


class TestHandleProviderMetricsForwardsParams:
    def test_handle_provider_metrics_forwards_timeframe(self):
        from orb.application.services.orchestration.dtos import GetProviderMetricsInput
        from orb.application.services.orchestration.get_provider_metrics import (
            GetProviderMetricsOrchestrator,
        )
        from orb.interface.system_command_handlers import handle_provider_metrics

        mock_orch = MagicMock(spec=GetProviderMetricsOrchestrator)
        mock_orch.execute = AsyncMock(return_value=MagicMock(metrics={}, message="ok"))
        container = _make_container(GetProviderMetricsOrchestrator=mock_orch)

        args = _make_args(provider=None, timeframe="1h")

        with patch("orb.interface.system_command_handlers.get_container", return_value=container):
            asyncio.run(handle_provider_metrics(args))

        mock_orch.execute.assert_called_once()
        call_input: GetProviderMetricsInput = mock_orch.execute.call_args[0][0]
        assert call_input.timeframe == "1h"


class TestHandleGetTemplateForwardsParams:
    def test_handle_get_template_forwards_provider_name(self):
        from orb.application.services.orchestration.dtos import GetTemplateInput
        from orb.application.services.orchestration.get_template import GetTemplateOrchestrator
        from orb.interface.template_command_handlers import handle_get_template

        mock_template = MagicMock()
        mock_template.model_dump.return_value = {"template_id": "t1"}
        mock_orch = MagicMock(spec=GetTemplateOrchestrator)
        mock_orch.execute = AsyncMock(return_value=MagicMock(template=mock_template))

        container = _make_container(GetTemplateOrchestrator=mock_orch)
        container.get.side_effect = lambda cls: (
            mock_orch if cls.__name__ == "GetTemplateOrchestrator" else MagicMock()
        )

        args = _make_args(template_id="t1", flag_template_id=None, provider_name="aws")

        with patch("orb.interface.template_command_handlers.get_container", return_value=container):
            asyncio.run(handle_get_template(args))

        mock_orch.execute.assert_called_once()
        call_input: GetTemplateInput = mock_orch.execute.call_args[0][0]
        assert call_input.provider_name == "aws"
