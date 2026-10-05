"""GCP instance/managed-instance status normalization, checked against the real SDK enums."""

import pytest
from google.cloud.compute_v1.types import Instance, ManagedInstance

from orb.providers.gcp.infrastructure.instance_status import (
    normalize_gcp_instance_status,
    normalize_gcp_managed_instance_status,
)

# Every member maps to a non-empty ORB status; this guards against the SDK
# adding a new enum member that silently falls through to "unknown".
_EXPECTED_INSTANCE_STATUS = {
    "UNDEFINED_STATUS": "unknown",
    "PENDING": "pending",
    "PROVISIONING": "pending",
    "STAGING": "launching",
    "RUNNING": "running",
    "REPAIRING": "pending",
    "STOPPING": "stopping",
    "STOPPED": "stopped",
    "SUSPENDING": "stopping",
    "SUSPENDED": "stopped",
    # A VM stopped via stop_instance() reports TERMINATED, not STOPPED, while
    # the resource still exists. Deletion is only observable as an absent
    # resource (404), never as this status value.
    "TERMINATED": "stopped",
    "DEPROVISIONING": "shutting-down",
}

_EXPECTED_CURRENT_ACTION = {
    "UNDEFINED_CURRENT_ACTION": "unknown",
    "NONE": "unknown",
    "CREATING": "pending",
    "CREATING_WITHOUT_RETRIES": "pending",
    "RECREATING": "pending",
    "REFRESHING": "pending",
    "STARTING": "launching",
    "RESUMING": "launching",
    "RESTARTING": "launching",
    "VERIFYING": "launching",
    "DELETING": "shutting-down",
    "ABANDONING": "shutting-down",
}


def test_instance_status_enum_fully_covered() -> None:
    """Every real SDK Instance.Status member must have an expected mapping."""
    sdk_members = {member.name for member in Instance.Status}
    assert sdk_members == set(_EXPECTED_INSTANCE_STATUS)


def test_current_action_enum_fully_covered() -> None:
    """Every real SDK ManagedInstance.CurrentAction member must be mapped."""
    sdk_members = {member.name for member in ManagedInstance.CurrentAction}
    assert sdk_members == set(_EXPECTED_CURRENT_ACTION)


@pytest.mark.parametrize("member", list(Instance.Status), ids=lambda m: m.name)
def test_normalize_gcp_instance_status_matches_real_sdk_enum(member: Instance.Status) -> None:
    assert normalize_gcp_instance_status(member.name) == _EXPECTED_INSTANCE_STATUS[member.name]


def test_normalize_gcp_instance_status_none_is_unknown() -> None:
    assert normalize_gcp_instance_status(None) == "unknown"


def test_normalize_gcp_instance_status_is_case_insensitive() -> None:
    assert normalize_gcp_instance_status("running") == "running"


def test_stopped_vm_reports_terminated_and_maps_to_stopped_not_terminated() -> None:
    """Regression test for the stop/delete conflation bug.

    A user-stopped VM is reported by Compute Engine as TERMINATED while the
    instance resource still exists; this must map to ORB's "stopped", not
    "terminated", so a stopped machine does not look deleted.
    """
    assert normalize_gcp_instance_status("TERMINATED") == "stopped"


@pytest.mark.parametrize("member", list(ManagedInstance.CurrentAction), ids=lambda m: m.name)
def test_normalize_gcp_managed_instance_status_matches_real_sdk_enum(
    member: ManagedInstance.CurrentAction,
) -> None:
    expected = _EXPECTED_CURRENT_ACTION[member.name]
    assert (
        normalize_gcp_managed_instance_status(instance_status=None, current_action=member.name)
        == expected
    )


def test_managed_instance_status_prefers_instance_status_over_current_action() -> None:
    """instance_status wins over current_action when both are known."""
    result = normalize_gcp_managed_instance_status(
        instance_status="RUNNING",
        current_action="DELETING",
    )
    assert result == "running"


def test_managed_instance_status_falls_back_to_current_action() -> None:
    result = normalize_gcp_managed_instance_status(
        instance_status=None,
        current_action="DELETING",
    )
    assert result == "shutting-down"


def test_managed_instance_status_unknown_when_both_absent() -> None:
    assert normalize_gcp_managed_instance_status(instance_status=None, current_action=None) == (
        "unknown"
    )
