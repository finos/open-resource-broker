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
        "acquiring": "running",
        "provisioning": "running",
        # PARTIAL_PENDING is a non-terminal holding state — still active, so it
        # maps to "running" for the HF wire contract.
        "partial_pending": "running",
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


def summarise_diagnostic(
    diagnostic: dict | None,
    *,
    fulfilled: int | None = None,
    target: int | None = None,
    fallback: str = "",
) -> str:
    """Build a category-templated HF ``message`` string from a diagnostic dict.

    The HostFactory response schema is strict (no room for a structured
    diagnostic field), so the *why* is folded into the existing ``message``
    string.  Category-appropriate, safe-to-surface templates are used — raw
    provider error messages are never emitted (only error *codes* for the
    UNKNOWN fallback), avoiding ARN / identifier leakage.

    Args:
        diagnostic: Serialised FulfilmentDiagnostic dict (or None).
        fulfilled: Fulfilled count, for capacity/deadline templates.
        target: Target count, for capacity/deadline templates.
        fallback: Message to use when there is no diagnostic.

    Returns:
        A short human-readable message string.
    """
    if not diagnostic:
        return fallback

    category = str(diagnostic.get("category", "unknown"))
    detail = diagnostic.get("detail")
    counts = ""
    if fulfilled is not None and target is not None:
        counts = f" {fulfilled}/{target}"

    if category == "capacity":
        return f"Partially fulfilled:{counts} (insufficient provider capacity)".strip()
    if category == "auth":
        return "Failed: provider authentication denied"
    if category == "rate_limit":
        return "Throttled by provider; retry recommended"
    if category == "validation":
        return f"Configuration error: {detail}" if detail else "Configuration error"
    if category == "cancelled":
        return diagnostic.get("summary") or fallback or "Request cancelled"
    if category == "deadline":
        return f"Deadline exceeded:{counts} fulfilled".strip()
    if category == "internal":
        return diagnostic.get("summary") or fallback or "Provisioning failed"

    # UNKNOWN — append provider error codes (not full messages) to the fallback.
    codes = sorted(
        {
            str(err.get("code"))
            for err in diagnostic.get("provider_errors", [])
            if isinstance(err, dict) and err.get("code")
        }
    )
    if codes:
        base = fallback or diagnostic.get("summary") or "Request incomplete"
        return f"{base} ({', '.join(codes)})"
    return fallback or diagnostic.get("summary") or "Request incomplete"


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
