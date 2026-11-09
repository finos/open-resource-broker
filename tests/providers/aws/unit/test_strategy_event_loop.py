"""Retry sleeps in the instance operations must not stall the event loop."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operations import Operation, OperationType
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from orb.providers.base.strategy import ProviderResult

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation_type, method",
    [
        (OperationType.TERMINATE_INSTANCES, "terminate_instances"),
        (OperationType.GET_INSTANCE_STATUS, "get_instance_status"),
        (OperationType.START_INSTANCES, "start_instances"),
        (OperationType.STOP_INSTANCES, "stop_instances"),
        (OperationType.CLEANUP_MACHINE_RESOURCES, "cleanup_machine_resources"),
        (OperationType.GET_MACHINE_HEALTH, "get_machine_health"),
    ],
)
async def test_blocking_instance_operations_leave_the_loop_responsive(operation_type, method):
    strategy = object.__new__(AWSProviderStrategy)
    service = MagicMock()

    def slow(operation):
        time.sleep(0.4)  # what a backoff sleep inside a retried call does
        return ProviderResult.success_result({})

    setattr(service, method, slow)
    strategy._get_instance_service = lambda: service  # type: ignore[method-assign]

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    try:
        result = await strategy._execute_operation_internal(Operation(operation_type, {}))
    finally:
        task.cancel()

    assert result.success
    # A blocked loop would have run the ticker once or not at all.
    assert ticks >= 10
