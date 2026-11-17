"""Slurm node names survive for every AWS provider API, not only synchronous ones.

``orb machines request <template> N --nodes compute-[001-002]`` stores the
expanded names in ``request.metadata["node_names"]``. The names are copied onto
Machine records (``name`` and tag ``orb:node-name``) only for the instances the
provider returns at creation time
(``RequestStatusManagementService.update_request_from_provisioning``) or, for
providers that return none at creation, when the status sync first records them
(``MachineSyncService.sync_machines_with_provider``). RunInstances and instant
EC2 Fleets take the first path; ASG, SpotFleet and request/maintain EC2 Fleets
take the second.

Each case drives create -> status sync through the CQRS buses against moto and
checks the stored Machine records.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.providers.aws.mocked.test_cqrs_control_loop import (  # noqa: F401
    _get_request,
    cqrs_buses,
    patch_moto_compat,
)

pytestmark = [pytest.mark.moto]

NODE_NAMES = ["compute-001", "compute-002"]

# One capacity unit per instance, so requested_count == number of instances.
# The generated templates weight t3.medium as 2 units, which would launch a
# single instance for two node names.
ONE_UNIT_PER_INSTANCE = {"machine_types": {"t3.medium": 1}}


@pytest.fixture
def named_buses(cqrs_buses, monkeypatch):  # noqa: F811
    """CQRS buses whose AWS handlers behave as they do against real AWS.

    ``AWSHandlerFactory.create_handler`` gives every handler an
    ``AWSMachineAdapter``. The shared moto fixture builds handlers without one,
    which sends describe results through the fallback payload (name = instance
    id, tags = {}) and would make every case lose its tag for a reason
    production does not have.

    Moto's RunInstances launches ``MinCount`` instances where EC2 launches up to
    ``MaxCount``; ORB sends ``MinCount=1``, so the call is raised to ``MaxCount``.
    """
    from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import (
        RunInstancesHandler,
    )
    from orb.providers.registry import get_provider_registry

    run_instances = RunInstancesHandler._run_instances

    def _launch_max_count(self: Any, run_params: dict[str, Any]) -> dict[str, Any]:
        run_params["MinCount"] = run_params["MaxCount"]
        return run_instances(self, run_params)

    monkeypatch.setattr(RunInstancesHandler, "_run_instances", _launch_max_count)

    strategy = get_provider_registry().get_or_create_strategy("aws_moto_eu-west-2")
    for handler in strategy._get_handler_registry()._handler_cache.values():
        handler._machine_adapter = AWSMachineAdapter(
            aws_client=handler.aws_client, logger=handler._logger
        )
    return cqrs_buses


def _machines(request_id: str) -> list[Any]:
    from orb.domain.base import UnitOfWorkFactory
    from orb.infrastructure.di.container import get_container

    with get_container().get(UnitOfWorkFactory).create_unit_of_work() as uow:
        return list(uow.machines.find_by_request_id(request_id))


def _snapshot(machines: list[Any]) -> list[tuple[str, Any, Any]]:
    return sorted((str(m.machine_id), m.name, m.tags.tags.get("orb:node-name")) for m in machines)


async def _create_named_request(command_bus: Any, template_id: str) -> str:
    from orb.application.dto.commands import CreateRequestCommand

    command = CreateRequestCommand(
        template_id=template_id,
        requested_count=len(NODE_NAMES),
        additional_data={"node_names": list(NODE_NAMES)},
        template_overrides=ONE_UNIT_PER_INSTANCE,
    )
    await command_bus.execute(command)
    assert command.created_request_id is not None
    return command.created_request_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template_id",
    [
        # Controls: the creation call returns the instances.
        pytest.param("RunInstances-OnDemand", id="RunInstances"),
        pytest.param("EC2Fleet-Instant-OnDemand", id="EC2Fleet-instant"),
        # Asynchronous fulfilment: instances are only found by the status sync.
        pytest.param("ASG-OnDemand", id="ASG"),
        pytest.param("SpotFleet-Request-LowestPrice", id="SpotFleet-request"),
        pytest.param("EC2Fleet-Request-OnDemand", id="EC2Fleet-request"),
        pytest.param("EC2Fleet-Maintain-OnDemand", id="EC2Fleet-maintain"),
    ],
)
async def test_machines_carry_slurm_node_names_after_sync(named_buses, template_id):
    command_bus, query_bus = named_buses

    request_id = await _create_named_request(command_bus, template_id)
    at_creation = _snapshot(_machines(request_id))
    await _get_request(query_bus, request_id)
    after_sync = _snapshot(_machines(request_id))

    observed = {"at_creation": at_creation, "after_sync": after_sync}
    # Without both instances the name check below would prove nothing.
    assert len(after_sync) == len(NODE_NAMES), observed
    assert sorted(name for _, name, _ in after_sync) == NODE_NAMES, observed
    assert sorted(tag for _, _, tag in after_sync) == NODE_NAMES, observed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "template_id",
    [
        pytest.param("RunInstances-OnDemand", id="RunInstances"),
        pytest.param("ASG-OnDemand", id="ASG"),
        pytest.param("SpotFleet-Request-LowestPrice", id="SpotFleet-request"),
        pytest.param("EC2Fleet-Maintain-OnDemand", id="EC2Fleet-maintain"),
    ],
)
async def test_instances_are_tagged_with_their_node_name(named_buses, template_id):
    """The EC2 instance carries ``orb:node-name``, which a compute node's boot
    script reads to start slurmd under its Slurm name."""
    import boto3

    from tests.providers.aws.mocked.test_cqrs_control_loop import REGION

    command_bus, query_bus = named_buses
    request_id = await _create_named_request(command_bus, template_id)
    await _get_request(query_bus, request_id)

    by_id = {str(m.machine_id): m.name for m in _machines(request_id)}
    reservations = boto3.client("ec2", region_name=REGION).describe_instances(
        InstanceIds=list(by_id)
    )["Reservations"]
    tagged = {
        i["InstanceId"]: {t["Key"]: t["Value"] for t in i.get("Tags", [])}.get("orb:node-name")
        for r in reservations
        for i in r["Instances"]
    }
    assert tagged == by_id
