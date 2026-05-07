"""Regression test: listing machines must preserve unique IDs after provider sync."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import ListMachinesQuery
from orb.application.queries.machine_query_handlers import ListMachinesHandler
from orb.domain.base.value_objects import InstanceType
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId
from orb.domain.machine.value_objects import MachineStatus


def _make_machine(instance_id: str) -> Machine:
    return Machine(
        machine_id=MachineId(value=instance_id),
        name=instance_id,
        status=MachineStatus.RUNNING,
        instance_type=InstanceType(value="t3.medium"),
        request_id="req-001",
        provider_name="aws_test",
        provider_type="aws",
        provider_api="RunInstances",
        resource_id="r-001",
        template_id="tmpl-001",
        image_id="ami-123",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_list_machines_sync_preserves_unique_ids():
    """Syncing during list should not replace all machines with the first synced machine."""
    m1 = _make_machine("i-aaa")
    m2 = _make_machine("i-bbb")
    m3 = _make_machine("i-ccc")

    mock_uow = MagicMock()
    mock_uow.machines.find_active_machines.return_value = [m1, m2, m3]
    mock_uow.requests.get_by_id.return_value = MagicMock(
        request_id="req-001",
        resource_ids=["r-001"],
        provider_api="RunInstances",
        provider_name="aws_test",
        provider_type="aws",
        template_id="tmpl-001",
    )

    mock_uow_factory = MagicMock()
    mock_uow_factory.create_unit_of_work.return_value.__enter__ = MagicMock(return_value=mock_uow)
    mock_uow_factory.create_unit_of_work.return_value.__exit__ = MagicMock(return_value=False)

    # Sync service returns all 3 machines (simulating AWS returning full reservation)
    mock_sync = AsyncMock()
    mock_sync.fetch_provider_machines.return_value = ([m1, m2, m3], {})
    mock_sync.sync_machines_with_provider.return_value = ([m1, m2, m3], {})

    handler = ListMachinesHandler(
        logger=MagicMock(),
        error_handler=MagicMock(),
        uow_factory=mock_uow_factory,
        container=MagicMock(),
        command_bus=MagicMock(),
        generic_filter_service=MagicMock(),
        machine_sync_service=mock_sync,
    )

    query = ListMachinesQuery(all_resources=True)
    result = await handler.execute_query(query)

    ids = [dto.machine_id for dto in result]
    assert ids == ["i-aaa", "i-bbb", "i-ccc"], f"Expected 3 unique IDs, got {ids}"
    assert len(set(ids)) == 3, "All machine IDs should be unique"
