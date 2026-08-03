"""CycleCloud resource identifier contract tests."""

import pytest

from orb.providers.azure.infrastructure.cyclecloud_resource_id import (
    CycleCloudMachineId,
    CycleCloudResourceId,
)


def test_cyclecloud_resource_id_round_trip():
    resource_id = CycleCloudResourceId(
        cluster_name="cluster/east",
        request_id="request:123",
    )

    serialized = str(resource_id)

    assert serialized == "cyclecloud://cluster%2Feast/requests/request%3A123"
    assert CycleCloudResourceId.parse(serialized) == resource_id


def test_cyclecloud_machine_id_round_trip():
    machine_id = CycleCloudMachineId(
        resource_id=CycleCloudResourceId(
            cluster_name="cluster/east",
            request_id="request:123",
        ),
        node_id="node/456",
    )

    serialized = str(machine_id)

    assert serialized == "cyclecloud://cluster%2Feast/requests/request%3A123/nodes/node%2F456"
    assert CycleCloudMachineId.parse(serialized) == machine_id


@pytest.mark.parametrize(
    "value",
    [
        "req-123",
        "cyclecloud://cluster-east/req-123",
        "cyclecloud://cluster-east/requests/",
        "cyclecloud://cluster-east/requests/req-123?unexpected=true",
    ],
)
def test_cyclecloud_resource_id_rejects_non_canonical_values(value):
    with pytest.raises(ValueError, match="CycleCloud resource ID"):
        CycleCloudResourceId.parse(value)


@pytest.mark.parametrize(
    "value",
    [
        "node-123",
        "cyclecloud://cluster-east/requests/req-123",
        "cyclecloud://cluster-east/requests/req-123/nodes/",
        "cyclecloud://cluster-east/requests/req-123/nodes/node-1?unexpected=true",
    ],
)
def test_cyclecloud_machine_id_rejects_non_canonical_values(value):
    with pytest.raises(ValueError, match="CycleCloud machine ID"):
        CycleCloudMachineId.parse(value)
