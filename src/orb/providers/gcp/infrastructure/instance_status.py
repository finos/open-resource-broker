"""GCP Compute Engine status normalization."""

from __future__ import annotations


def normalize_gcp_instance_status(status: str | None) -> str:
    """Map Compute Engine instance statuses to ORB machine statuses.

    GCP conflates "stopped" and "deleted" at the status-string level: a VM
    stopped via ``stop_instance`` (or the console/CLI/gcloud equivalent)
    reports ``TERMINATED`` while the instance resource still exists and can
    be restarted. Compute Engine does not expose a distinct "deleted"
    status on the instance resource itself — deletion is only observable as
    the absence of the resource (a 404/``NotFound`` from ``get_instance``).
    Callers that poll by instance ID and get nothing back are the ones
    responsible for inferring deletion from that absence (see
    ``GCPSingleVMHandler.check_hosts_status``, which drops not-found
    instances from its result rather than returning a status, and
    ``MachineSyncService``, which treats an instance missing from a
    provider response as terminated). This function only ever sees
    instances that still exist, so ``TERMINATED`` is mapped to
    ``"stopped"`` here rather than ``"terminated"``.
    """
    if status is None:
        return "unknown"

    status_map = {
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
        # Stopped-but-still-existing instance; see the docstring above.
        "TERMINATED": "stopped",
        # Transitional state while the instance is being torn down.
        "DEPROVISIONING": "shutting-down",
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
        "UNDEFINED_CURRENT_ACTION": "unknown",
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
        "NONE": "unknown",
    }
    return action_map.get(current_action.upper(), "unknown")
