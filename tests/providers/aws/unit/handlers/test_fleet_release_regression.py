"""Regression tests for fleet release manager bugs.

These tests are written RED-first (TDD) to prove the bugs exist before any fix.

Bugs under test:
- EC2Fleet request-type partial return: delete_fleets is called instead of modify_fleet
- SpotFleet request-type partial return: cancel_spot_fleet_requests is called instead of
  modify_spot_fleet_request
- SpotFleet maintain-type: enum vs string comparison bug causes modify to be skipped
"""

from unittest.mock import Mock

import pytest

from orb.providers.aws.domain.template.value_objects import AWSFleetType
from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
    EC2FleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.release_manager import (
    SpotFleetReleaseManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ec2_fleet_manager() -> tuple[EC2FleetReleaseManager, Mock]:
    """Return (manager, mock_aws_client) with a no-op retry that calls directly."""
    aws_client = Mock()
    aws_ops = Mock()
    aws_ops.terminate_instances_with_fallback = Mock()
    logger = Mock()

    def _direct_retry(func, operation_type: str = "standard", **kwargs):  # type: ignore[return]
        return func(**kwargs)

    def _noop_paginate(method, result_key, **kwargs):  # type: ignore[return]
        return []

    def _noop_collect(**kwargs):  # type: ignore[return]
        return []

    manager = EC2FleetReleaseManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        config_port=None,
        logger=logger,
        retry_fn=_direct_retry,
        paginate_fn=_noop_paginate,
        collect_with_next_token_fn=_noop_collect,
        cleanup_on_zero_capacity_fn=Mock(),
    )
    return manager, aws_client


def _make_spot_fleet_manager() -> tuple[SpotFleetReleaseManager, Mock]:
    """Return (manager, mock_aws_client) with AWSOperations._retry_with_backoff stubbed."""
    aws_client = Mock()
    aws_ops = Mock()
    aws_ops.terminate_instances_with_fallback = Mock()

    # SpotFleetReleaseManager calls self._aws_ops._retry_with_backoff when present.
    # Stub it to call the function directly so no real retry logic runs.
    def _direct_retry_with_backoff(func, operation_type: str = "standard", **kwargs):  # type: ignore[return]
        return func(**kwargs)

    aws_ops._retry_with_backoff = _direct_retry_with_backoff

    logger = Mock()

    manager = SpotFleetReleaseManager(
        aws_client=aws_client,
        aws_ops=aws_ops,
        request_adapter=None,
        cleanup_on_zero_capacity_fn=Mock(),
        logger=logger,
    )
    return manager, aws_client


def _request_fleet_details_ec2(total_target_capacity: int) -> dict:
    """Minimal EC2 Fleet describe_fleets entry for a request-type fleet."""
    return {
        "FleetId": "fleet-001",
        "Type": "request",
        "TargetCapacitySpecification": {
            "TotalTargetCapacity": total_target_capacity,
            "OnDemandTargetCapacity": 0,
            "SpotTargetCapacity": total_target_capacity,
        },
        "Tags": [],
    }


def _request_fleet_details_spot(target_capacity: int) -> dict:
    """Minimal SpotFleet describe_spot_fleet_requests entry for a request-type fleet."""
    return {
        "SpotFleetRequestId": "sfr-001",
        "SpotFleetRequestConfig": {
            "Type": "request",
            "TargetCapacity": target_capacity,
            "OnDemandTargetCapacity": 0,
        },
        "Tags": [],
    }


def _maintain_fleet_details_spot(target_capacity: int) -> dict:
    """Minimal SpotFleet describe_spot_fleet_requests entry for a maintain-type fleet.

    Uses the AWSFleetType enum value so we can test the enum-vs-string comparison bug.
    """
    return {
        "SpotFleetRequestId": "sfr-002",
        "SpotFleetRequestConfig": {
            # Deliberately use the enum member to surface the comparison bug:
            # fleet_type = str(fleet_config.get("Type", "maintain")).lower()
            # then compared against AWSFleetType.MAINTAIN (which is "maintain")
            # The bug is that str(AWSFleetType.MAINTAIN).lower() == "awsfleettype.maintain"
            # not "maintain", so the comparison fails.
            "Type": AWSFleetType.MAINTAIN,
            "TargetCapacity": target_capacity,
            "OnDemandTargetCapacity": 0,
        },
        "Tags": [],
    }


# ---------------------------------------------------------------------------
# EC2 Fleet tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEC2FleetRequestPartialReturn:
    """EC2Fleet request-type fleet: partial return must reduce capacity, not delete."""

    def test_ec2_fleet_request_partial_return_does_not_delete_fleet(self):
        """BUG: delete_fleets is called on a partial return of a request fleet.

        Expected: delete_fleets NOT called when returning 1 of 3 instances.
        Current behaviour: delete_fleets IS called unconditionally for request fleets.

        This test MUST FAIL against the unfixed code.
        """
        manager, aws_client = _make_ec2_fleet_manager()
        fleet_details = _request_fleet_details_ec2(total_target_capacity=3)

        manager.release(
            fleet_id="fleet-001",
            instance_ids=["i-001"],
            fleet_details=fleet_details,
        )

        aws_client.ec2_client.delete_fleets.assert_not_called()

    def test_ec2_fleet_request_partial_return_does_not_modify_capacity(self):
        """Request fleets are fire-and-forget — no capacity update needed on partial return.

        Expected: modify_fleet NOT called, delete_fleets NOT called.
        Only instances are terminated.
        """
        manager, aws_client = _make_ec2_fleet_manager()
        fleet_details = _request_fleet_details_ec2(total_target_capacity=3)

        manager.release(
            fleet_id="fleet-001",
            instance_ids=["i-001"],
            fleet_details=fleet_details,
        )

        aws_client.ec2_client.modify_fleet.assert_not_called()
        aws_client.ec2_client.delete_fleets.assert_not_called()

    def test_ec2_fleet_request_full_return_deletes_fleet(self):
        """Sanity check: full return of a request fleet SHOULD delete the fleet.

        This test MUST PASS against both unfixed and fixed code.
        """
        manager, aws_client = _make_ec2_fleet_manager()
        fleet_details = _request_fleet_details_ec2(total_target_capacity=1)

        manager.release(
            fleet_id="fleet-001",
            instance_ids=["i-001"],
            fleet_details=fleet_details,
        )

        aws_client.ec2_client.delete_fleets.assert_called_once_with(
            FleetIds=["fleet-001"],
            TerminateInstances=False,
        )


# ---------------------------------------------------------------------------
# SpotFleet tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSpotFleetRequestPartialReturn:
    """SpotFleet request-type fleet: partial return must reduce capacity, not cancel."""

    def test_spot_fleet_request_partial_return_does_not_cancel_fleet(self):
        """BUG: cancel_spot_fleet_requests is called on a partial return of a request fleet.

        Expected: cancel_spot_fleet_requests NOT called when returning 1 of 3 instances.
        Current behaviour: cancel_spot_fleet_requests IS called unconditionally for
        request-type fleets after instance termination.

        This test MUST FAIL against the unfixed code.
        """
        manager, aws_client = _make_spot_fleet_manager()
        fleet_details = _request_fleet_details_spot(target_capacity=3)

        manager.release(
            fleet_id="sfr-001",
            instance_ids=["i-001"],
            fleet_details=fleet_details,
        )

        aws_client.ec2_client.cancel_spot_fleet_requests.assert_not_called()

    def test_spot_fleet_request_partial_return_does_not_modify_capacity(self):
        """Request fleets are fire-and-forget — no capacity update needed on partial return.

        Expected: modify_spot_fleet_request NOT called, cancel_spot_fleet_requests NOT called.
        Only instances are terminated.
        """
        manager, aws_client = _make_spot_fleet_manager()
        fleet_details = _request_fleet_details_spot(target_capacity=3)

        manager.release(
            fleet_id="sfr-001",
            instance_ids=["i-001"],
            fleet_details=fleet_details,
        )

        aws_client.ec2_client.modify_spot_fleet_request.assert_not_called()
        aws_client.ec2_client.cancel_spot_fleet_requests.assert_not_called()


@pytest.mark.unit
class TestSpotFleetMaintainEnumBug:
    """SpotFleet maintain-type fleet: enum value stored in fleet config breaks comparison."""

    def test_spot_fleet_maintain_fleet_type_comparison(self):
        """BUG: AWSFleetType enum stored as Type in fleet config breaks the string comparison.

        The release manager does:
            fleet_type = str(fleet_config.get("Type", "maintain")).lower()
            if fleet_type == AWSFleetType.MAINTAIN: ...

        When the stored Type is the AWSFleetType.MAINTAIN enum member,
        str(AWSFleetType.MAINTAIN).lower() produces "awsfleettype.maintain" (or similar),
        not "maintain", so the comparison fails and modify_spot_fleet_request is never called.

        Expected: modify_spot_fleet_request IS called for a maintain fleet with enum Type.
        Current behaviour: the branch is skipped entirely.

        This test MUST FAIL against the unfixed code.
        """
        manager, aws_client = _make_spot_fleet_manager()
        fleet_details = _maintain_fleet_details_spot(target_capacity=3)

        manager.release(
            fleet_id="sfr-002",
            instance_ids=["i-002"],
            fleet_details=fleet_details,
        )

        aws_client.ec2_client.modify_spot_fleet_request.assert_called_once_with(
            SpotFleetRequestId="sfr-002",
            TargetCapacity=2,
            OnDemandTargetCapacity=0,
        )
