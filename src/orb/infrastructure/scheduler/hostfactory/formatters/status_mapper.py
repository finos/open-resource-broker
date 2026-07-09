"""Stateless HostFactory status-mapping functions.

These three pure functions translate between internal domain status strings
and the HostFactory API's ``status`` / ``result`` / ``message`` fields as
specified in ``hf_docs/input-output.md``.

All functions are module-level (no ``self`` dependency) so they can be
imported and unit-tested independently of ``HostFactorySchedulerStrategy``.
"""

from __future__ import annotations


def map_domain_status_to_hostfactory(domain_status: str) -> str:
    """Map a domain request status to the HostFactory ``status`` field.

    Per HostFactory docs the possible values are:
    ``'running'``, ``'complete'``, ``'complete_with_error'``.

    Args:
        domain_status: Internal domain status string (e.g. ``"pending"``,
            ``"in_progress"``, ``"complete"``, ``"failed"``).

    Returns:
        One of ``'running'``, ``'complete'``, or ``'complete_with_error'``.
    """
    status_mapping: dict[str, str] = {
        "pending": "running",
        "in_progress": "running",
        "provisioning": "running",
        "complete": "complete",
        "completed": "complete",
        "partial": "complete_with_error",
        "failed": "complete_with_error",
        "cancelled": "complete_with_error",
        "timeout": "complete_with_error",
        "error": "complete_with_error",
    }
    return status_mapping.get(domain_status.lower(), "running")


def map_machine_status_to_result(status: str | None, request_type: str | None = None) -> str:
    """Map a machine status to the HostFactory ``result`` field.

    Per HostFactory docs the possible values are:
    ``'executing'``, ``'fail'``, ``'succeed'``.

    Args:
        status: Machine lifecycle status (e.g. ``"running"``, ``"pending"``,
            ``"terminated"``).
        request_type: Optional request context — ``"return"`` flips the
            success/fail semantics so that ``"terminated"`` maps to
            ``"succeed"`` rather than ``"fail"``.

    Returns:
        One of ``'executing'``, ``'fail'``, or ``'succeed'``.
    """
    if request_type == "return":
        # For return requests: terminated/stopped = success, in-flight = executing
        if status in ["terminated", "stopped"]:
            return "succeed"
        elif status in ["shutting-down", "stopping", "pending", "terminating", "running"]:
            return "executing"
        else:
            return "fail"
    # For acquire requests, running is success
    elif status == "running":
        return "succeed"
    elif status in ["pending", "launching"]:
        return "executing"
    elif status in ["terminated", "failed", "error"]:
        return "fail"
    else:
        return "executing"  # Default for unknown states


def generate_status_message(status: str, machine_count: int) -> str:
    """Generate an appropriate human-readable status message.

    HostFactory examples show an empty string for terminal-success and
    in-progress states; non-empty messages are reserved for partial
    fulfilment and failures.

    Args:
        status: Domain request status string.
        machine_count: Number of machines associated with the request.

    Returns:
        A short status message string (may be empty).
    """
    if status == "completed":
        return ""  # HostFactory examples show empty message for success
    elif status == "partial":
        return f"Partially fulfilled: {machine_count} instances created"
    elif status == "failed":
        return "Failed to create instances"
    elif status in ["pending", "in_progress", "provisioning"]:
        return ""  # HostFactory examples show empty message for running
    else:
        return ""
