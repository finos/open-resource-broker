"""GCP Compute Engine status normalization."""

from __future__ import annotations


def normalize_gcp_instance_status(status: str | None) -> str:
    """Map Compute Engine instance statuses to ORB machine statuses."""
    if status is None:
        return "unknown"

    status_map = {
        "PROVISIONING": "pending",
        "STAGING": "launching",
        "RUNNING": "running",
        "STOPPING": "stopping",
        "SUSPENDING": "stopping",
        "SUSPENDED": "stopped",
        "REPAIRING": "pending",
        "TERMINATED": "terminated",
    }
    return status_map.get(status.upper(), "unknown")


def normalize_gcp_managed_instance_status(
    *,
    instance_status: str | None,
    current_action: str | None,
) -> str:
    """Map MIG managed-instance status/action fields to ORB machine statuses."""
    normalized_status = normalize_gcp_instance_status(instance_status)
    if normalized_status != "unknown":
        return normalized_status

    if current_action is None:
        return "unknown"

    action_map = {
        "CREATING": "pending",
        "CREATING_WITHOUT_RETRIES": "pending",
        "RECREATING": "pending",
        "REFRESHING": "pending",
        "RESTARTING": "launching",
        "VERIFYING": "launching",
        "DELETING": "shutting-down",
        "ABANDONING": "shutting-down",
        "NONE": "unknown",
    }
    return action_map.get(current_action.upper(), "unknown")
