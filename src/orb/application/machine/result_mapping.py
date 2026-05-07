"""Machine status → HostFactory result mapping.

Single source of truth for the status→result translation used across the
application layer (DTOs, query handlers) and infrastructure formatters.
"""

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class ResultStatus(str, Enum):
    """Result status values for machine references."""

    SUCCEED = "succeed"
    EXECUTING = "executing"
    FAIL = "fail"


def map_machine_status_to_result(status: str | None, request_type: str | None = None) -> str:
    """Map machine status to HostFactory result field.

    Args:
        status: Domain machine status string (e.g. ``"running"``, ``"pending"``).
        request_type: Optional request type string.  When ``"return"``, terminated
            and stopped states map to ``"succeed"`` rather than ``"fail"``.

    Returns:
        One of ``"succeed"``, ``"executing"``, or ``"fail"``.
    """
    if request_type == "return":
        if status in ["terminated", "stopped"]:
            return ResultStatus.SUCCEED
        elif status in ["shutting-down", "stopping", "pending", "terminating", "running"]:
            return ResultStatus.EXECUTING
        else:
            return ResultStatus.FAIL
    elif status == "running":
        return ResultStatus.SUCCEED
    elif status in ["pending", "launching"]:
        return ResultStatus.EXECUTING
    elif status in ["terminated", "failed", "error"]:
        return ResultStatus.FAIL
    else:
        logger.warning("Unknown machine status %r; defaulting result to 'executing'", status)
        return ResultStatus.EXECUTING
