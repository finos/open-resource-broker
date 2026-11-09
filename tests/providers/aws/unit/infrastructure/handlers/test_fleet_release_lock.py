"""Concurrent returns on one fleet are serialised and each reduction is applied."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.providers.aws.infrastructure.handlers import fleet_release_lock
from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
    EC2FleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.fleet_release_lock import (
    FleetReleaseBusyError,
    active_entries,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.release_manager import (
    SpotFleetReleaseManager,
)

pytestmark = pytest.mark.unit

DESCRIBE_DELAY = 0.15


def _retry(fn, operation_type="standard", operation_name=None, **kwargs):
    if callable(fn) and not kwargs and operation_type != "critical":
        try:
            return fn()
        except TypeError:
            pass
    return fn(**kwargs)


class _FleetState:
    """A fleet target that slow describes and ordered modifies act on."""

    def __init__(self, target: int) -> None:
        self.target = target
        self.modifies: list[int] = []
        self.in_modify = 0
        self.max_in_modify = 0

    def describe_fleets(self, **_: Any) -> dict[str, Any]:
        time.sleep(DESCRIBE_DELAY)
        return {
            "Fleets": [
                {
                    "FleetId": "fleet-1",
                    "Type": "maintain",
                    "FleetState": "active",
                    "TargetCapacitySpecification": {"TotalTargetCapacity": self.target},
                    "Tags": [{"Key": "orb:request-id", "Value": "req-1"}],
                }
            ]
        }

    def modify_fleet(self, **kwargs: Any) -> dict[str, Any]:
        self.in_modify += 1
        self.max_in_modify = max(self.max_in_modify, self.in_modify)
        time.sleep(0.02)
        self.target = kwargs["TargetCapacitySpecification"]["TotalTargetCapacity"]
        self.modifies.append(self.target)
        self.in_modify -= 1
        return {}

    def describe_spot_fleet_requests(self, **_: Any) -> dict[str, Any]:
        time.sleep(DESCRIBE_DELAY)
        return {
            "SpotFleetRequestConfigs": [
                {
                    "SpotFleetRequestState": "active",
                    "SpotFleetRequestConfig": {
                        "Type": "maintain",
                        "TargetCapacity": self.target,
                        "OnDemandTargetCapacity": 0,
                    },
                }
            ]
        }

    def modify_spot_fleet_request(self, **kwargs: Any) -> dict[str, Any]:
        self.in_modify += 1
        self.max_in_modify = max(self.max_in_modify, self.in_modify)
        time.sleep(0.02)
        self.target = kwargs["TargetCapacity"]
        self.modifies.append(self.target)
        self.in_modify -= 1
        return {}


def _ec2_client(state: _FleetState) -> MagicMock:
    ec2 = MagicMock()
    ec2.describe_fleets.side_effect = state.describe_fleets
    ec2.modify_fleet.side_effect = state.modify_fleet
    ec2.describe_spot_fleet_requests.side_effect = state.describe_spot_fleet_requests
    ec2.modify_spot_fleet_request.side_effect = state.modify_spot_fleet_request
    ec2.describe_fleet_instances.return_value = {"ActiveInstances": [{"InstanceId": "i-keep"}]}
    ec2.describe_spot_fleet_instances.return_value = {"ActiveInstances": [{"InstanceId": "i-keep"}]}
    return ec2


def _ec2_manager(ec2: Any) -> EC2FleetReleaseManager:
    aws_client = MagicMock()
    aws_client.ec2_client = ec2
    return EC2FleetReleaseManager(
        aws_client=aws_client,
        aws_ops=MagicMock(),
        request_adapter=None,
        config_port=None,
        logger=MagicMock(),
        retry_fn=_retry,
        paginate_fn=lambda method, key, **kw: method(**kw).get(key, []),
        collect_with_next_token_fn=lambda method, key, **kw: method(**kw).get(key, []),
        cleanup_on_zero_capacity_fn=MagicMock(),
    )


def _spot_manager(ec2: Any) -> SpotFleetReleaseManager:
    aws_client = MagicMock()
    aws_client.ec2_client = ec2
    return SpotFleetReleaseManager(
        aws_client=aws_client,
        aws_ops=MagicMock(),
        request_adapter=None,
        cleanup_on_zero_capacity_fn=MagicMock(),
        logger=MagicMock(),
        retry_fn=_retry,
    )


def _run_concurrently(calls: list[Any]) -> list[Exception]:
    errors: list[Exception] = []
    start = threading.Event()

    def worker(call: Any) -> None:
        start.wait()
        try:
            call()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(timeout=30)
    return errors


class TestConcurrentReturnsOfOneFleet:
    def test_ec2_fleet_both_reductions_are_applied(self):
        state = _FleetState(target=5)
        manager = _ec2_manager(_ec2_client(state))

        errors = _run_concurrently(
            [
                lambda: manager.release("fleet-1", ["i-1"], {}),
                lambda: manager.release("fleet-1", ["i-2"], {}),
            ]
        )

        assert errors == []
        assert state.target == 3
        assert state.modifies == [4, 3]
        assert state.max_in_modify == 1

    def test_spot_fleet_both_reductions_are_applied(self):
        state = _FleetState(target=5)
        manager = _spot_manager(_ec2_client(state))

        errors = _run_concurrently(
            [
                lambda: manager.release("sfr-1", ["i-1"], {}),
                lambda: manager.release("sfr-1", ["i-2"], {}),
            ]
        )

        assert errors == []
        assert state.target == 3
        assert state.modifies == [4, 3]

    def test_details_supplied_by_the_caller_do_not_decide_the_reduction(self):
        state = _FleetState(target=2)
        manager = _ec2_manager(_ec2_client(state))
        stale = {"Type": "maintain", "TargetCapacitySpecification": {"TotalTargetCapacity": 5}}

        manager.release("fleet-1", ["i-1"], stale)

        assert state.target == 1

    def test_different_fleets_do_not_wait_for_each_other(self):
        state_a, state_b = _FleetState(5), _FleetState(5)
        manager_a = _ec2_manager(_ec2_client(state_a))
        manager_b = _ec2_manager(_ec2_client(state_b))

        started = time.monotonic()
        errors = _run_concurrently(
            [
                lambda: manager_a.release("fleet-a", ["i-1"], {}),
                lambda: manager_b.release("fleet-b", ["i-1"], {}),
            ]
        )

        assert errors == []
        # Each release spends several describe delays; serialised they would take twice as long.
        assert time.monotonic() - started < 5 * DESCRIBE_DELAY * 2


class TestLockLifecycle:
    def test_entries_are_removed_when_idle(self):
        state = _FleetState(target=5)
        manager = _ec2_manager(_ec2_client(state))
        before = active_entries()

        manager.release("fleet-1", ["i-1"], {})

        assert active_entries() == before

    def test_entries_are_removed_after_a_failed_release(self):
        ec2 = _ec2_client(_FleetState(5))
        ec2.describe_fleets.side_effect = RuntimeError("boom")
        manager = _ec2_manager(ec2)
        before = active_entries()

        with pytest.raises(RuntimeError):
            manager.release("fleet-1", ["i-1"], {})

        assert active_entries() == before

    def test_a_waiter_that_times_out_raises_and_drops_its_reference(self):
        manager = _ec2_manager(_ec2_client(_FleetState(5)))
        manager._lock_timeout = 0.05
        before = active_entries()
        holding = threading.Event()
        let_go = threading.Event()

        def holder() -> None:
            with fleet_release_lock.fleet_release_lock("fleet-1"):
                holding.set()
                let_go.wait(timeout=10)

        thread = threading.Thread(target=holder)
        thread.start()
        assert holding.wait(timeout=5)
        try:
            with pytest.raises(FleetReleaseBusyError):
                manager.release("fleet-1", ["i-1"], {})
            # Only the holder's entry remains, and it is still usable.
            assert active_entries() == before + 1
        finally:
            let_go.set()
            thread.join(timeout=10)

        assert active_entries() == before

    def test_waiters_are_bounded_by_the_timeout(self):
        before = active_entries()
        holding = threading.Event()
        let_go = threading.Event()

        def holder() -> None:
            with fleet_release_lock.fleet_release_lock("fleet-9"):
                holding.set()
                let_go.wait(timeout=10)

        thread = threading.Thread(target=holder)
        thread.start()
        assert holding.wait(timeout=5)
        outcomes: list[str] = []

        def waiter() -> None:
            try:
                with fleet_release_lock.fleet_release_lock("fleet-9", timeout=0.1):
                    outcomes.append("acquired")
            except FleetReleaseBusyError:
                outcomes.append("busy")

        started = time.monotonic()
        waiters = [threading.Thread(target=waiter) for _ in range(8)]
        for w in waiters:
            w.start()
        for w in waiters:
            w.join(timeout=5)
        elapsed = time.monotonic() - started
        let_go.set()
        thread.join(timeout=10)

        assert outcomes == ["busy"] * 8
        assert elapsed < 2
        assert active_entries() == before


class TestPostWriteDetector:
    def test_warns_when_the_target_read_back_differs(self):
        state = _FleetState(target=5)
        ec2 = _ec2_client(state)
        manager = _ec2_manager(ec2)
        real_modify = state.modify_fleet

        def modify_then_other_writer(**kwargs: Any) -> dict[str, Any]:
            real_modify(**kwargs)
            state.target = 1  # another process wrote in between
            return {}

        ec2.modify_fleet.side_effect = modify_then_other_writer

        manager.release("fleet-1", ["i-1"], {})

        warnings = [str(c.args[0]) for c in manager._logger.warning.call_args_list]
        assert any("another release or modification" in w for w in warnings)

    def test_is_silent_when_the_target_is_as_set(self):
        state = _FleetState(target=5)
        manager = _ec2_manager(_ec2_client(state))

        manager.release("fleet-1", ["i-1"], {})

        warnings = [str(c.args[0]) for c in manager._logger.warning.call_args_list]
        assert not any("another release or modification" in w for w in warnings)


class TestDetailsUnderTheLock:
    def test_a_failing_describe_falls_back_to_the_supplied_details(self):
        state = _FleetState(target=5)
        ec2 = _ec2_client(state)
        ec2.describe_fleets.side_effect = RuntimeError("throttled")
        manager = _ec2_manager(ec2)
        supplied = {
            "Type": "maintain",
            "FleetState": "active",
            "TargetCapacitySpecification": {"TotalTargetCapacity": 5},
        }

        manager.release("fleet-1", ["i-1"], supplied)

        warnings = [str(c.args[0]) for c in manager._logger.warning.call_args_list]
        assert any("using the details the caller supplied" in w for w in warnings)
        manager._aws_ops.terminate_instances_with_fallback.assert_called_once()

    def test_a_failing_describe_without_supplied_details_propagates_and_frees_the_lock(self):
        ec2 = _ec2_client(_FleetState(5))
        ec2.describe_fleets.side_effect = RuntimeError("throttled")
        manager = _ec2_manager(ec2)
        before = active_entries()

        with pytest.raises(RuntimeError, match="throttled"):
            manager.release("fleet-1", ["i-1"], {})

        assert active_entries() == before
        # The lock is usable again straight away.
        with fleet_release_lock.fleet_release_lock("fleet-1", timeout=0.1):
            pass

    def test_an_empty_describe_means_the_fleet_is_gone_even_with_stale_details(self):
        ec2 = _ec2_client(_FleetState(5))
        ec2.describe_fleets.side_effect = None
        ec2.describe_fleets.return_value = {"Fleets": []}
        manager = _ec2_manager(ec2)
        stale = {
            "Type": "maintain",
            "FleetState": "active",
            "TargetCapacitySpecification": {"TotalTargetCapacity": 5},
        }

        result = manager.release("fleet-1", ["i-1"], stale, request_id="req-1")

        assert result is True
        ec2.modify_fleet.assert_not_called()
        ec2.delete_fleets.assert_not_called()
        manager._aws_ops.terminate_instances_with_fallback.assert_called_once()
        manager._cleanup_on_zero_capacity.assert_called_once_with("ec2_fleet", "req-1", ["i-1"])
