"""The shared test reset clears scheduler registrations between tests."""

from unittest.mock import MagicMock

import pytest

from orb.infrastructure.scheduler.registry import get_scheduler_registry
from tests.utilities.reset_singletons import reset_all_singletons

pytestmark = pytest.mark.unit


def test_reset_all_singletons_drops_scheduler_registrations() -> None:
    registry = get_scheduler_registry()
    if not registry.is_registered("reset-probe"):
        registry.register(
            "reset-probe",
            lambda cfg: MagicMock(),
            lambda c: None,
            strategy_class=MagicMock,
        )
    assert get_scheduler_registry().is_registered("reset-probe")

    reset_all_singletons()

    fresh = get_scheduler_registry()
    assert fresh is not registry
    assert not fresh.is_registered("reset-probe")
