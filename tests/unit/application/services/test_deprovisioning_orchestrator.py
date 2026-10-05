"""Unit tests for DeprovisioningOrchestrator.

Covers:
- Parallel execution across resource groups with mixed success/failure.
- Exception results from gather() counted as failures.
- Top-level exception handling in execute_deprovisioning.
- _process_resource_group success and template-not-found paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.deprovisioning_orchestrator import DeprovisioningOrchestrator


def _make_machine(machine_id: str, template_id: str = "tmpl-1", request_id: str = "req-1"):
    machine = MagicMock()
    machine.machine_id.value = machine_id
    machine.template_id = template_id
    machine.request_id = request_id
    return machine


_UNSET = object()


def _make_orchestrator(
    query_bus_result: object = _UNSET,
    provider_selection_result: object = None,
) -> tuple[DeprovisioningOrchestrator, MagicMock]:
    if query_bus_result is _UNSET:
        query_bus_result = MagicMock()
    uow_factory = MagicMock()
    logger = MagicMock()
    container = MagicMock()
    container.get.return_value = MagicMock()

    query_bus = MagicMock()
    query_bus.execute = AsyncMock(return_value=query_bus_result)

    provider_selection_port = MagicMock()
    if provider_selection_result is None:
        provider_selection_result = MagicMock(success=True, error_message=None)
    provider_selection_port.execute_operation = AsyncMock(return_value=provider_selection_result)

    orchestrator = DeprovisioningOrchestrator(
        uow_factory=uow_factory,
        logger=logger,
        container=container,
        query_bus=query_bus,
        provider_selection_port=provider_selection_port,
    )
    return orchestrator, provider_selection_port


@pytest.mark.unit
class TestExecuteDeprovisioning:
    @pytest.mark.asyncio
    async def test_all_groups_succeed(self):
        orchestrator, _ = _make_orchestrator()
        request = MagicMock(request_id="req-correlation")
        resource_groups = {
            ("aws", "EC2Fleet", "res-1"): [_make_machine("i-1")],
            ("aws", "EC2Fleet", "res-2"): [_make_machine("i-2")],
        }

        result = await orchestrator.execute_deprovisioning(resource_groups, request)

        assert result["success"] is True
        assert result["successful_operations"] == 2
        assert result["failed_operations"] == 0
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_mixed_success_and_failure(self):
        orchestrator, provider_selection_port = _make_orchestrator()
        request = MagicMock(request_id="req-correlation")

        async def _execute_operation(provider_name, operation):
            resource_id = operation.parameters["resource_id"]
            if resource_id == "res-fail":
                return MagicMock(success=False, error_message="boom")
            return MagicMock(success=True, error_message=None)

        provider_selection_port.execute_operation = AsyncMock(side_effect=_execute_operation)

        resource_groups = {
            ("aws", "EC2Fleet", "res-ok"): [_make_machine("i-1")],
            ("aws", "EC2Fleet", "res-fail"): [_make_machine("i-2")],
        }

        result = await orchestrator.execute_deprovisioning(resource_groups, request)

        assert result["success"] is False
        assert result["successful_operations"] == 1
        assert result["failed_operations"] == 1
        assert "boom" in result["errors"]

    @pytest.mark.asyncio
    async def test_exception_in_task_counted_as_failure(self):
        orchestrator, provider_selection_port = _make_orchestrator()
        provider_selection_port.execute_operation = AsyncMock(
            side_effect=RuntimeError("provider down")
        )
        request = MagicMock(request_id="req-correlation")
        resource_groups = {
            ("aws", "EC2Fleet", "res-1"): [_make_machine("i-1")],
        }

        result = await orchestrator.execute_deprovisioning(resource_groups, request)

        assert result["success"] is False
        assert result["failed_operations"] == 1
        assert result["successful_operations"] == 0

    @pytest.mark.asyncio
    async def test_empty_resource_groups_is_noop_success(self):
        orchestrator, _ = _make_orchestrator()
        request = MagicMock(request_id="req-correlation")

        result = await orchestrator.execute_deprovisioning({}, request)

        assert result["success"] is True
        assert result["successful_operations"] == 0
        assert result["failed_operations"] == 0


@pytest.mark.unit
class TestProcessResourceGroup:
    @pytest.mark.asyncio
    async def test_success_returns_terminated_count(self):
        orchestrator, _ = _make_orchestrator()
        request = MagicMock(request_id="req-correlation")
        machines = [_make_machine("i-1"), _make_machine("i-2")]

        result = await orchestrator._process_resource_group(
            "aws", "EC2Fleet", "res-1", machines, request
        )

        assert result["success"] is True
        assert result["terminated_instances"] == 2

    @pytest.mark.asyncio
    async def test_template_not_found_raises_and_is_caught(self):
        orchestrator, _ = _make_orchestrator(query_bus_result=None)
        request = MagicMock(request_id="req-correlation")
        machines = [_make_machine("i-1")]

        result = await orchestrator._process_resource_group(
            "aws", "EC2Fleet", "res-1", machines, request
        )

        assert result["success"] is False
        assert "Template not found" in result["error_message"]

    @pytest.mark.asyncio
    async def test_provider_failure_returns_error_message(self):
        orchestrator, _ = _make_orchestrator(
            provider_selection_result=MagicMock(success=False, error_message="rejected")
        )
        request = MagicMock(request_id="req-correlation")
        machines = [_make_machine("i-1")]

        result = await orchestrator._process_resource_group(
            "aws", "EC2Fleet", "res-1", machines, request
        )

        assert result["success"] is False
        assert result["error_message"] == "rejected"
