"""Unit tests for BaseFleetReleaseManager shared release flow.

These tests exercise the base class using a minimal concrete subclass with
mocked abstract methods so a future third fleet type has a working template.

The tests verify:
- release() delegates to the correct abstract methods in the right order.
- The teardown flag logic (request-type guard, maintain-type weighted fallback).
- Instant-fleet unconditional teardown path.
- Empty instance_ids → full fleet cancel/delete.
- Exception propagation.
"""

from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from orb.providers.aws.infrastructure.handlers.base_fleet_release import BaseFleetReleaseManager
from orb.providers.aws.infrastructure.handlers.fleet_release_policy import (
    FleetCapacityInput,
    FleetReleaseDecision,
)

# ---------------------------------------------------------------------------
# Minimal concrete implementation for testing
# ---------------------------------------------------------------------------


class _ConcreteFleetReleaseManager(BaseFleetReleaseManager):
    """Minimal concrete subclass that delegates every abstract method to a Mock."""

    def __init__(self):
        aws_client = MagicMock()
        aws_ops = MagicMock()
        aws_ops.terminate_instances_with_fallback = MagicMock()
        logger = MagicMock()
        cleanup_fn = MagicMock()

        super().__init__(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=None,
            cleanup_on_zero_capacity_fn=cleanup_fn,
            logger=logger,
            retry_fn=lambda fn, operation_type="standard", **kw: fn(**kw),
        )

        # Expose concrete mock handles for test assertions.
        self.mock_cleanup_fn = cleanup_fn

        # Abstract method mocks — tests patch these directly.
        self._fleet_label_mock = MagicMock(return_value="Test Fleet")
        self._fetch_fleet_details_mock = MagicMock(return_value={})
        self._extract_capacity_input_mock: MagicMock | None = None
        self._reduce_capacity_mock = MagicMock()
        self._terminate_instances_mock = MagicMock()
        self._cancel_or_delete_fleet_mock = MagicMock()
        self._fleet_has_no_remaining_instances_mock = MagicMock(return_value=False)
        self._zero_capacity_mock = MagicMock()
        self._cleanup_launch_template_mock = MagicMock()

    # Abstract method implementations that delegate to mocks.

    def _fleet_label(self) -> str:
        return self._fleet_label_mock()

    def _fetch_fleet_details(self, fleet_id: str) -> dict[str, Any]:
        return self._fetch_fleet_details_mock(fleet_id)

    def _extract_capacity_input(
        self,
        fleet_id: str,
        fleet_details: dict[str, Any],
        instance_ids: list[str],
    ) -> tuple[FleetCapacityInput, dict[str, Any]]:
        if self._extract_capacity_input_mock is not None:
            return self._extract_capacity_input_mock(fleet_id, fleet_details, instance_ids)
        # Default: maintain-type, full return.
        inp = FleetCapacityInput(
            fleet_type="maintain",
            target_capacity_units=len(instance_ids),
            instances_to_return_count=len(instance_ids),
            instance_weighted_capacity_units=len(instance_ids),
        )
        return inp, {}

    def _reduce_capacity(
        self,
        fleet_id: str,
        capacity_input: FleetCapacityInput,
        extra: dict[str, Any],
        decision: FleetReleaseDecision,
    ) -> None:
        self._reduce_capacity_mock(fleet_id, capacity_input, extra, decision)

    def _terminate_instances(self, fleet_id: str, instance_ids: list[str]) -> None:
        self._terminate_instances_mock(fleet_id, instance_ids)

    def _cancel_or_delete_fleet(
        self,
        fleet_id: str,
        terminate_instances: bool,
        is_maintain: bool = False,
    ) -> None:
        self._cancel_or_delete_fleet_mock(fleet_id, terminate_instances, is_maintain)

    def _fleet_has_no_remaining_instances(self, fleet_id: str, excluded_ids: set[str]) -> bool:
        return self._fleet_has_no_remaining_instances_mock(fleet_id, excluded_ids)

    def _zero_capacity(self, fleet_id: str) -> None:
        self._zero_capacity_mock(fleet_id)

    def _cleanup_launch_template(
        self,
        fleet_details: dict[str, Any],
        request_id: str = "",
    ) -> None:
        self._cleanup_launch_template_mock(fleet_details, request_id)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _capacity_input(
    fleet_type: str = "maintain",
    target: int = 2,
    count: int = 2,
    weighted: int = 2,
) -> FleetCapacityInput:
    return FleetCapacityInput(
        fleet_type=fleet_type,
        target_capacity_units=target,
        instances_to_return_count=count,
        instance_weighted_capacity_units=weighted,
    )


# ---------------------------------------------------------------------------
# Tests: basic flow
# ---------------------------------------------------------------------------


class TestBaseFleetReleaseManagerSharedFlow:
    def test_maintain_full_return_calls_reduce_terminate_cancel(self):
        """Maintain full return: reduce capacity → terminate → cancel fleet → cleanup LT."""
        mgr = _ConcreteFleetReleaseManager()
        mgr._fleet_has_no_remaining_instances_mock.return_value = True
        fleet_details = {"Type": "maintain"}

        def _mock_extract(fleet_id, details, iids):
            return _capacity_input("maintain", 2, 2, 2), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        mgr.release("fleet-1", ["i-1", "i-2"], fleet_details)

        mgr._reduce_capacity_mock.assert_called_once()
        mgr._terminate_instances_mock.assert_called_once_with("fleet-1", ["i-1", "i-2"])
        mgr._cancel_or_delete_fleet_mock.assert_called_once()
        mgr._cleanup_launch_template_mock.assert_called_once()

    def test_maintain_partial_return_does_not_cancel(self):
        """Maintain partial return: reduce capacity → terminate → NO cancel/delete."""
        mgr = _ConcreteFleetReleaseManager()
        mgr._fleet_has_no_remaining_instances_mock.return_value = False
        fleet_details = {"Type": "maintain"}

        def _mock_extract(fleet_id, details, iids):
            return _capacity_input("maintain", target=4, count=1, weighted=1), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        mgr.release("fleet-1", ["i-1"], fleet_details)

        mgr._reduce_capacity_mock.assert_called_once()
        mgr._terminate_instances_mock.assert_called_once()
        mgr._cancel_or_delete_fleet_mock.assert_not_called()
        mgr._cleanup_launch_template_mock.assert_not_called()

    def test_request_full_return_with_empty_fleet_cancels(self):
        """Request full return AND fleet empty → cancel fleet + cleanup."""
        mgr = _ConcreteFleetReleaseManager()
        mgr._fleet_has_no_remaining_instances_mock.return_value = True  # fleet empty
        fleet_details = {"Type": "request"}

        def _mock_extract(fleet_id, details, iids):
            return _capacity_input("request", target=2, count=2, weighted=2), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        mgr.release("fleet-r", ["i-1", "i-2"], fleet_details)

        # No capacity reduction for request fleets.
        mgr._reduce_capacity_mock.assert_not_called()
        mgr._terminate_instances_mock.assert_called_once()
        # Fleet IS cancelled because helper confirms empty.
        mgr._cancel_or_delete_fleet_mock.assert_called_once()
        mgr._cleanup_launch_template_mock.assert_called_once()

    def test_request_full_arithmetic_but_instances_remain_does_not_cancel(self):
        """Request fleet: is_full_return=True from arithmetic but instances still running.

        The _fleet_has_no_remaining_instances guard returns False (instances remain) so
        the fleet must NOT be cancelled even though capacity arithmetic says full return.
        """
        mgr = _ConcreteFleetReleaseManager()
        mgr._fleet_has_no_remaining_instances_mock.return_value = False  # instances remain

        def _mock_extract(fleet_id, details, iids):
            # Returning 1 instance of weight=4 from a target=4 fleet → is_full_return=True
            return _capacity_input("request", target=4, count=1, weighted=4), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        mgr.release("fleet-r", ["i-1"], {"Type": "request"})

        mgr._cancel_or_delete_fleet_mock.assert_not_called()
        mgr._cleanup_launch_template_mock.assert_not_called()

    def test_instant_fleet_unconditional_teardown(self):
        """Instant fleet (has_fleet_record=False): always attempt delete + cleanup."""
        mgr = _ConcreteFleetReleaseManager()

        def _mock_extract(fleet_id, details, iids):
            return _capacity_input("instant", target=1, count=1, weighted=1), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        mgr.release("fleet-i", ["i-1"], {"Type": "instant"})

        # Instant fleets: no _fleet_has_no_remaining_instances check.
        mgr._fleet_has_no_remaining_instances_mock.assert_not_called()
        # But delete IS attempted.
        mgr._cancel_or_delete_fleet_mock.assert_called_once()
        mgr._cleanup_launch_template_mock.assert_called_once()

    def test_empty_instance_ids_full_fleet_cancel(self):
        """Empty instance_ids → immediate fleet cancel (no instance termination)."""
        mgr = _ConcreteFleetReleaseManager()
        fleet_details = {"Type": "maintain"}

        mgr.release("fleet-1", [], fleet_details)

        mgr._terminate_instances_mock.assert_not_called()
        # is_maintain defaults to False for the empty-instance_ids full-teardown path.
        mgr._cancel_or_delete_fleet_mock.assert_called_once_with("fleet-1", True, False)
        mgr._cleanup_launch_template_mock.assert_called_once()

    def test_fetch_fleet_details_called_when_details_empty(self):
        """When fleet_details is empty, _fetch_fleet_details is called."""
        mgr = _ConcreteFleetReleaseManager()
        fetched = {"Type": "maintain"}
        mgr._fetch_fleet_details_mock.return_value = fetched

        def _mock_extract(fleet_id, details, iids):
            assert details is fetched, "base class should pass the fetched details"
            return _capacity_input("maintain", 1, 1, 1), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        mgr.release("fleet-1", ["i-1"], {})

        mgr._fetch_fleet_details_mock.assert_called_once_with("fleet-1")

    def test_exception_during_release_is_logged_and_reraised(self):
        """Exceptions from abstract methods are logged with fleet label and re-raised."""
        mgr = _ConcreteFleetReleaseManager()
        mgr._terminate_instances_mock.side_effect = RuntimeError("network error")

        def _mock_extract(fleet_id, details, iids):
            return _capacity_input("maintain", 1, 1, 1), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        with pytest.raises(RuntimeError, match="network error"):
            mgr.release("fleet-err", ["i-1"], {"Type": "maintain"})

        assert cast(MagicMock, mgr._logger.error).call_count >= 1


# ---------------------------------------------------------------------------
# Tests: maintain weighted-capacity fallback
# ---------------------------------------------------------------------------


class TestMaintainWeightedCapacityFallback:
    def test_maintain_partial_arithmetic_but_empty_fleet_cancels_after_zero(self):
        """Maintain fleet: is_full_return=False from arithmetic but fleet is physically empty.

        _fleet_has_no_remaining_instances returns True → fleet IS cancelled.
        _zero_capacity is called first to prevent replacement launches.
        """
        mgr = _ConcreteFleetReleaseManager()
        # Arithmetic says partial (target=4, returning weight=1 instance).
        # Live check confirms fleet is empty.
        mgr._fleet_has_no_remaining_instances_mock.return_value = True

        def _mock_extract(fleet_id, details, iids):
            return _capacity_input("maintain", target=4, count=1, weighted=1), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        mgr.release("fleet-m", ["i-1"], {"Type": "maintain"})

        mgr._zero_capacity_mock.assert_called_once_with("fleet-m")
        mgr._cancel_or_delete_fleet_mock.assert_called_once()
        mgr._cleanup_launch_template_mock.assert_called_once()

    def test_maintain_partial_arithmetic_instances_remain_no_cancel(self):
        """Maintain fleet: is_full_return=False AND fleet still has instances → no cancel."""
        mgr = _ConcreteFleetReleaseManager()
        mgr._fleet_has_no_remaining_instances_mock.return_value = False

        def _mock_extract(fleet_id, details, iids):
            return _capacity_input("maintain", target=4, count=1, weighted=1), {}

        mgr._extract_capacity_input_mock = MagicMock(side_effect=_mock_extract)

        mgr.release("fleet-m", ["i-1"], {"Type": "maintain"})

        mgr._zero_capacity_mock.assert_not_called()
        mgr._cancel_or_delete_fleet_mock.assert_not_called()
        mgr._cleanup_launch_template_mock.assert_not_called()
