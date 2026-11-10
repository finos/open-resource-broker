"""SchedulerStrategyFactory.invalidate drops cached strategies."""

from unittest.mock import MagicMock

from orb.infrastructure.scheduler.factory import SchedulerStrategyFactory


def test_invalidate_makes_the_next_call_build_a_new_strategy():
    factory = SchedulerStrategyFactory()
    registry = MagicMock()
    registry.create_strategy.side_effect = lambda *_: object()
    factory._scheduler_registry = registry
    config = object()

    first = factory.create_strategy("default", config)
    assert factory.create_strategy("default", config) is first

    factory.invalidate()

    assert factory.create_strategy("default", config) is not first
