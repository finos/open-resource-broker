"""An acquire request is never completed while a machine it created is still pending."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, Mock

import pytest

from orb.application.services.provisioning_orchestration_service import ProvisioningResult
from orb.application.services.request_status_management_service import (
    RequestStatusManagementService,
)
from orb.domain.base.operation_outcome import Accepted, Completed
from orb.domain.machine.machine_status import MachineStatus
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import AWSFleetType
from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler
from orb.providers.aws.infrastructure.handlers.run_instances.handler import RunInstancesHandler
from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

pytestmark = [pytest.mark.unit]


def _pending_machine(instance_id: str, **_: Any) -> dict[str, Any]:
    return {"instance_id": instance_id, "status": "pending"}


def _fleet_handler(fleet_type: AWSFleetType) -> tuple[Any, Any]:
    ops = Mock()
    ops.execute_with_standard_error_handling = Mock(
        return_value={"fleet_id": "fleet-1", "instance_ids": [], "metadata_updates": {}}
    )
    config = Mock()
    config.get_resource_prefix.return_value = ""
    handler = EC2FleetHandler(
        aws_client=Mock(),
        logger=Mock(),
        aws_ops=ops,
        launch_template_manager=Mock(),
        config_port=config,
    )
    handler._validate_prerequisites = Mock()
    return handler, SimpleNamespace(template_id="t", fleet_type=fleet_type, provider_api=None)


def _run_instances() -> tuple[Any, Any]:
    response = {
        "ReservationId": "r-1",
        "Instances": [{"InstanceId": "i-1"}, {"InstanceId": "i-2"}],
    }
    ops = Mock()
    ops.execute_with_standard_error_handling = Mock(return_value=response)
    adapter = Mock()
    adapter.create_machine_from_aws_instance.side_effect = lambda data, *a, **k: _pending_machine(
        data["InstanceId"]
    )
    handler = RunInstancesHandler(
        aws_client=Mock(),
        logger=Mock(),
        aws_ops=ops,
        launch_template_manager=Mock(),
        machine_adapter=adapter,
    )
    handler._validate_prerequisites = Mock()
    return handler, Mock(template_id="t")


def _spot_fleet() -> tuple[Any, Any]:
    ops = Mock()
    ops.execute_with_standard_error_handling = Mock(return_value={"SpotFleetRequestId": "sfr-1"})
    handler = SpotFleetHandler(
        aws_client=Mock(), logger=Mock(), aws_ops=ops, launch_template_manager=Mock()
    )
    handler._validate_prerequisites = Mock()
    template = Mock()
    template.spot_fleet_role = "arn:aws:iam::123456789012:role/SpotFleetRole"
    template.fleet_type = None
    return handler, template


def _asg() -> tuple[Any, Any]:
    ops = Mock()
    ops.execute_with_standard_error_handling = Mock(return_value="asg-1")
    handler = ASGHandler(
        aws_client=Mock(), logger=Mock(), aws_ops=ops, launch_template_manager=Mock()
    )
    handler._validate_prerequisites = Mock()
    handler.launch_template_manager.create_or_update_launch_template.return_value = SimpleNamespace(
        template_id="lt", version="1"
    )
    return handler, Mock(template_id="t")


CASES = {
    "run_instances": _run_instances,
    "ec2_fleet_instant": lambda: _fleet_handler(AWSFleetType.INSTANT),
    "ec2_fleet_request": lambda: _fleet_handler(AWSFleetType.REQUEST),
    "ec2_fleet_maintain": lambda: _fleet_handler(AWSFleetType.MAINTAIN),
    "spot_fleet": _spot_fleet,
    "asg": _asg,
}


def _service_and_saved() -> tuple[RequestStatusManagementService, list[Any]]:
    saved: list[Any] = []
    uow = MagicMock()
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    uow.machines.save_batch.side_effect = lambda machines: saved.extend(machines)
    factory = MagicMock()
    factory.create_unit_of_work.return_value = uow
    return RequestStatusManagementService(uow_factory=factory, logger=MagicMock()), saved


def _acquire_request(count: int) -> Request:
    return Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="t",
        machine_count=count,
        provider_type="aws",
        provider_name="aws-prod",
        provider_api="RunInstances",
    )


def _as_orchestrated(handler_result: dict[str, Any]) -> ProvisioningResult:
    """Build the provisioning result the way the dispatch layer does."""
    provider_data = dict(handler_result["provider_data"])
    resource_ids = handler_result["resource_ids"]
    if provider_data.get("requires_async_polling", False):
        outcome: Any = Accepted(
            request_id="r", pending_resource_ids=resource_ids, metadata=provider_data
        )
    else:
        outcome = Completed(resource_ids=resource_ids, metadata=provider_data)
    instances = handler_result.get("instances", [])
    return ProvisioningResult(
        success=True,
        resource_ids=resource_ids,
        machine_ids=handler_result.get("instance_ids", []),
        instances=instances,
        provider_data=provider_data,
        fulfilled_count=len(instances),
        outcome=outcome,
    )


@pytest.mark.parametrize("case", CASES)
@pytest.mark.asyncio
async def test_request_is_not_completed_while_machines_are_pending(case):
    handler, template = CASES[case]()
    count = 2
    request = _acquire_request(count)
    handler_result = handler._acquire_hosts_internal(
        cast(Request, request), cast(AWSTemplate, template)
    )

    service, saved = _service_and_saved()
    updated = await service.update_request_from_provisioning(
        request, _as_orchestrated(handler_result)
    )

    pending = [m for m in saved if m.status in (MachineStatus.PENDING, MachineStatus.LAUNCHING)]
    if pending:
        assert updated.status != RequestStatus.COMPLETED
        assert updated.status == RequestStatus.IN_PROGRESS
    # APIs that return no machines at creation are always still in progress.
    if not saved:
        assert updated.status == RequestStatus.IN_PROGRESS


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "launching"])
async def test_completed_outcome_with_unsettled_instances_stays_in_progress(status):
    request = _acquire_request(2)
    instances = [{"instance_id": f"i-{n}", "status": status} for n in (1, 2)]
    result = ProvisioningResult(
        success=True,
        resource_ids=["r-1"],
        machine_ids=["i-1", "i-2"],
        instances=instances,
        provider_data={},
        fulfilled_count=2,
        outcome=Completed(resource_ids=["r-1"], metadata={}),
    )
    service, _ = _service_and_saved()

    updated = await service.update_request_from_provisioning(request, result)

    assert updated.status == RequestStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_completed_outcome_with_running_instances_completes():
    request = _acquire_request(1)
    result = ProvisioningResult(
        success=True,
        resource_ids=["r-1"],
        machine_ids=["i-1"],
        instances=[{"instance_id": "i-1", "status": "running"}],
        provider_data={},
        fulfilled_count=1,
        outcome=Completed(resource_ids=["r-1"], metadata={}),
    )
    service, _ = _service_and_saved()

    updated = await service.update_request_from_provisioning(request, result)

    assert updated.status == RequestStatus.COMPLETED
