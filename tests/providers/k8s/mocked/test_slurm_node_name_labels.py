"""Slurm node names reach Kubernetes pods as labels.

``orb machines request <template> N --nodes k8s-[001-002]`` stores the names on
the request. The Pod handler returns no machines from the create call, so the
status sync names the machines and pushes ``orb:node-name`` through
``TAG_INSTANCES``. The Kubernetes provider writes it as the pod label
``orb.io/node-name``, which a compute container reads through the downward API.

Drives create -> status sync through the CQRS buses against kmock and reads the
labels back from the emulated apiserver.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.providers.k8s.mocked import test_cqrs_control_loop

pytestmark = pytest.mark.kmock

NODE_NAMES = ["k8s-001", "k8s-002"]

# The CQRS buses wired to kmock, shared with the control-loop test.
buses = test_cqrs_control_loop.buses


def _machines(request_id: str) -> list[Any]:
    from orb.domain.base import UnitOfWorkFactory
    from orb.infrastructure.di.container import get_container

    with get_container().get(UnitOfWorkFactory).create_unit_of_work() as uow:
        return list(uow.machines.find_by_request_id(request_id))


@pytest.mark.asyncio
async def test_pods_are_labelled_with_their_node_name(buses, kmock_k8s) -> None:
    import kmock

    from orb.application.dto.commands import CreateRequestCommand
    from orb.application.dto.queries import SyncAndGetRequestQuery

    command_bus, query_bus, _ = buses
    command = CreateRequestCommand(
        template_id="k8s-pod-example",
        requested_count=len(NODE_NAMES),
        additional_data={"node_names": list(NODE_NAMES)},
    )
    await command_bus.execute(command)
    request_id = command.created_request_id
    assert request_id is not None

    await query_bus.execute(SyncAndGetRequestQuery(request_id=request_id))

    names = {str(m.machine_id): m.name for m in _machines(request_id)}
    assert sorted(names.values()) == NODE_NAMES, names
    pods = kmock.resource("", "v1", "pods")
    labels = {
        key[2]: (obj.raw.get("metadata", {}).get("labels") or {}).get("orb.io/node-name")
        for key, obj in kmock_k8s.objects.items()
        if key[0] == pods and key[2] in names
    }
    assert labels == names
