"""Operator confirmation that instances of a return request are terminated.

Some providers answer "not found" both for an instance that no longer exists
and for one the caller may not read, so a return request can never observe the
termination on its own. An operator who has verified the instances are gone
records that fact on the request; the provider then honours it for exactly
those machines, and only while the provider keeps answering "not found".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from orb.domain.base.exceptions import BusinessRuleViolationError, ValidationError
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestType

CONFIRMED_TERMINATED_KEY = "operator_confirmed_terminated"

# Providers whose "not found" answer is ambiguous and so accept confirmations.
CONFIRMABLE_PROVIDER_TYPES = frozenset({"oci"})

MAX_REASON_LENGTH = 500
MAX_MACHINE_IDS = 100

_CONTROL_CHARS = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")


class InstanceStillVisibleError(BusinessRuleViolationError):
    """The provider still shows an instance that is not terminated."""


def sanitize_reason(reason: str | None) -> str:
    """Strip control characters and surrounding space; reject empty or oversized text."""
    cleaned = " ".join(_CONTROL_CHARS.sub(" ", reason or "").split())
    if not cleaned:
        raise ValidationError("A reason is required")
    if len(cleaned) > MAX_REASON_LENGTH:
        raise ValidationError(f"Reason must be at most {MAX_REASON_LENGTH} characters")
    return cleaned


def sanitize_identity(value: str | None, default: str = "anonymous") -> str:
    """Make a principal label safe for logs and storage; never empty."""
    cleaned = " ".join(_CONTROL_CHARS.sub(" ", value or "").split())
    return cleaned[:200] or default


def normalize_machine_ids(machine_ids: Iterable[str] | None) -> list[str]:
    """Return the IDs de-duplicated in order; require at least one and bound the count."""
    unique: list[str] = []
    for machine_id in machine_ids or []:
        cleaned = str(machine_id).strip()
        if cleaned and cleaned not in unique:
            unique.append(cleaned)
    if not unique:
        raise ValidationError("At least one machine ID is required")
    if len(unique) > MAX_MACHINE_IDS:
        raise ValidationError(f"At most {MAX_MACHINE_IDS} machine IDs can be confirmed at once")
    return unique


def validate_confirmable(request: Request, machine_ids: list[str]) -> None:
    """Raise ``ValidationError`` unless every ID can be confirmed on ``request``."""
    request_id = str(request.request_id)
    if request.request_type != RequestType.RETURN:
        raise ValidationError(f"Request {request_id} is not a return request")
    if request.provider_type not in CONFIRMABLE_PROVIDER_TYPES:
        raise ValidationError(
            f"Request {request_id} uses provider '{request.provider_type}', "
            "which does not need termination confirmation"
        )
    if request.status.is_terminal():
        raise ValidationError(
            f"Request {request_id} is already {request.status.value}; nothing to confirm"
        )
    known = set(request.machine_ids)
    strays = [machine_id for machine_id in machine_ids if machine_id not in known]
    if strays:
        raise ValidationError(
            f"Machines not part of return request {request_id}: {', '.join(strays)}"
        )


def confirmed_records(request: Request) -> list[dict[str, Any]]:
    """Return the confirmation records stored on the request."""
    records = (request.metadata or {}).get(CONFIRMED_TERMINATED_KEY) or []
    return [r for r in records if isinstance(r, dict)]


def confirmed_machine_ids(metadata: Mapping[str, Any] | None) -> set[str]:
    """Return the machine IDs an operator has confirmed terminated."""
    records = (metadata or {}).get(CONFIRMED_TERMINATED_KEY) or []
    return {str(r["machine_id"]) for r in records if isinstance(r, dict) and r.get("machine_id")}


def build_record(
    machine_id: str,
    *,
    operator: str,
    role: str,
    reason: str,
    verification: str,
) -> dict[str, str]:
    """Build one audit record for a confirmed machine."""
    return {
        "machine_id": machine_id,
        "operator": operator,
        "role": role,
        "reason": reason,
        "verification": verification,
        "at": datetime.now(timezone.utc).isoformat(),
    }
