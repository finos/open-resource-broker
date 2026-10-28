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
        assert result["partial"] is False
        assert result["successful_instance_ids"] == []
        assert result["failed_instance_ids"] == ["i-1"]
        assert result["error_message"] == "rejected"

    @pytest.mark.asyncio
    async def test_partial_termination_preserves_succeeded_and_failed_instance_ids(self):
        partial_provider_result = MagicMock(
            success=False,
            error_message="Azure termination partially failed",
            error_code="AZURE_TERMINATION_PARTIAL_FAILURE",
            metadata={
                "operation": "terminate_instances",
                "instance_ids": ["vm-1", "vm-2"],
                "method": "handler",
                "provider_data": {
                    "successful_instance_ids": ["vm-1"],
                    "failed_instance_ids": ["vm-2"],
                },
            },
        )
        orchestrator, _ = _make_orchestrator(provider_selection_result=partial_provider_result)
        request = MagicMock(request_id="req-correlation")
        machines = [_make_machine("vm-1"), _make_machine("vm-2")]

        group_result = await orchestrator._process_resource_group(
            "azure", "VMSS", "vmss-1", machines, request
        )

        assert group_result["success"] is False
        assert group_result["partial"] is True
        assert group_result["terminated_instances"] == 1
        assert group_result["successful_instance_ids"] == ["vm-1"]
        assert group_result["failed_instance_ids"] == ["vm-2"]
        assert group_result["error_message"] == "Azure termination partially failed"

        overall = await orchestrator.execute_deprovisioning(
            {("azure", "VMSS", "vmss-1"): machines}, request
        )

        assert overall["success"] is False
        assert overall["partial"] is True
        assert overall["successful_instance_ids"] == ["vm-1"]
        assert overall["failed_instance_ids"] == ["vm-2"]
        assert "Azure termination partially failed" in overall["errors"]

    @pytest.mark.asyncio
    async def test_partial_termination_infers_successful_ids_when_only_failed_ids_reported(self):
        partial_provider_result = MagicMock(
            success=False,
            error_message="GCP MIG partial delete failure",
            metadata={
                "provider_data": {
                    "failed_instance_ids": ["mig-vm-2"],
                },
            },
        )
        orchestrator, _ = _make_orchestrator(provider_selection_result=partial_provider_result)
        request = MagicMock(request_id="req-correlation")
        machines = [_make_machine("mig-vm-1"), _make_machine("mig-vm-2"), _make_machine("mig-vm-3")]

        group_result = await orchestrator._process_resource_group(
            "gcp", "MIG", "mig-1", machines, request
        )

        assert group_result["success"] is False
        assert group_result["partial"] is True
        assert group_result["terminated_instances"] == 2
        assert group_result["successful_instance_ids"] == ["mig-vm-1", "mig-vm-3"]
        assert group_result["failed_instance_ids"] == ["mig-vm-2"]

    @pytest.mark.asyncio
    async def test_partial_termination_infers_failed_ids_when_only_successful_ids_reported(self):
        partial_provider_result = MagicMock(
            success=False,
            error_message="Azure VMSS partial delete failure",
            metadata={
                "provider_data": {
                    "successful_instance_ids": ["vm-1"],
                },
            },
        )
        orchestrator, _ = _make_orchestrator(provider_selection_result=partial_provider_result)
        request = MagicMock(request_id="req-correlation")
        machines = [_make_machine("vm-1"), _make_machine("vm-2")]

        group_result = await orchestrator._process_resource_group(
            "azure", "VMSS", "vmss-1", machines, request
        )

        assert group_result["success"] is False
        assert group_result["partial"] is True
        assert group_result["successful_instance_ids"] == ["vm-1"]
        assert group_result["failed_instance_ids"] == ["vm-2"]

    @pytest.mark.asyncio
    async def test_partial_termination_treats_unlisted_instances_as_failed_when_both_lists_returned(
        self,
    ):
        partial_provider_result = MagicMock(
            success=False,
            error_message="Azure VMSS partial delete with omitted instance",
            metadata={
                "provider_data": {
                    "successful_instance_ids": ["vm-1"],
                    "failed_instance_ids": ["vm-2"],
                },
            },
        )
        orchestrator, _ = _make_orchestrator(provider_selection_result=partial_provider_result)
        request = MagicMock(request_id="req-correlation")
        machines = [_make_machine("vm-1"), _make_machine("vm-2"), _make_machine("vm-3")]

        group_result = await orchestrator._process_resource_group(
            "azure", "VMSS", "vmss-1", machines, request
        )

        assert group_result["success"] is False
        assert group_result["partial"] is True
        assert group_result["terminated_instances"] == 1
        assert group_result["successful_instance_ids"] == ["vm-1"]
        assert group_result["failed_instance_ids"] == ["vm-2", "vm-3"]

        overall = await orchestrator.execute_deprovisioning(
            {("azure", "VMSS", "vmss-1"): machines}, request
        )

        assert overall["success"] is False
        assert overall["partial"] is True
        assert overall["successful_instance_ids"] == ["vm-1"]
        assert overall["failed_instance_ids"] == ["vm-2", "vm-3"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "metadata_payload",
        [
            None,
            {"provider_data": None},
            {"provider_data": "not-a-dict"},
            {"provider_data": 42},
            {"provider_data": {"successful_instance_ids": 123, "failed_instance_ids": {"bad": 1}}},
        ],
    )
    async def test_non_list_or_missing_provider_data_falls_back_to_total_group_failure(
        self, metadata_payload
    ):
        failed_provider_result = MagicMock(
            success=False,
            error_message="provider error",
            metadata=metadata_payload,
        )
        orchestrator, _ = _make_orchestrator(provider_selection_result=failed_provider_result)
        request = MagicMock(request_id="req-correlation")
        machines = [_make_machine("vm-1"), _make_machine("vm-2")]

        group_result = await orchestrator._process_resource_group(
            "azure", "VMSS", "vmss-1", machines, request
        )

        assert group_result["success"] is False
        assert group_result["partial"] is False
        assert group_result["successful_instance_ids"] == []
        assert group_result["failed_instance_ids"] == ["vm-1", "vm-2"]

    @pytest.mark.asyncio
    async def test_multi_group_deprovisioning_aggregates_success_partial_and_total_failure(self):
        orchestrator, provider_selection_port = _make_orchestrator()
        request = MagicMock(request_id="req-correlation")

        async def _execute_operation(provider_name, operation):
            resource_id = operation.parameters["resource_id"]
            if resource_id == "rg-ok":
                return MagicMock(success=True, error_message=None, metadata={})
            if resource_id == "rg-partial":
                return MagicMock(
                    success=False,
                    error_message="partial group error",
                    metadata={
                        "provider_data": {
                            "successful_instance_ids": ["vm-p1"],
                            "failed_instance_ids": ["vm-p2"],
                        }
                    },
                )
            return MagicMock(
                success=False,
                error_message="total group error",
                metadata={"provider_data": None},
            )

        provider_selection_port.execute_operation = AsyncMock(side_effect=_execute_operation)
        resource_groups = {
            ("azure", "VMSS", "rg-ok"): [_make_machine("vm-ok-1")],
            ("azure", "VMSS", "rg-partial"): [_make_machine("vm-p1"), _make_machine("vm-p2")],
            ("azure", "VMSS", "rg-fail"): [_make_machine("vm-fail-1")],
        }

        overall = await orchestrator.execute_deprovisioning(resource_groups, request)

        assert overall["success"] is False
        assert overall["partial"] is True
        assert overall["successful_operations"] == 1
        assert overall["failed_operations"] == 2
        assert overall["successful_instance_ids"] == ["vm-ok-1", "vm-p1"]
        assert overall["failed_instance_ids"] == ["vm-p2", "vm-fail-1"]
        assert "partial group error" in overall["errors"]
        assert "total group error" in overall["errors"]


@pytest.mark.unit
class TestMachineCoordinatesForwarded:
    @pytest.mark.asyncio
    async def test_terminate_operation_includes_persisted_machine_coordinates(self):
        from unittest.mock import patch

        orchestrator, provider_selection_port = _make_orchestrator()
        request = MagicMock(request_id="req-correlation")
        coordinates = {
            "i-1": {
                "provider_api": "MIG",
                "resource_id": "mig-a",
                "provider_data": {"scope": "zonal", "zone": "us-central1-b"},
            }
        }

        with patch(
            "orb.application.services.deprovisioning_orchestrator.load_machine_coordinates",
            return_value=coordinates,
        ):
            await orchestrator.execute_deprovisioning(
                {("gcp", "MIG", "mig-a"): [_make_machine("i-1")]}, request
            )

        operation = provider_selection_port.execute_operation.call_args.args[1]
        assert operation.parameters["machine_coordinates"] == coordinates
