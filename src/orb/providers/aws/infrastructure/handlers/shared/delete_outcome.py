"""Helpers for interpreting the outcome of fleet / ASG delete calls.

Delete and cancel APIs for EC2 Fleet and Spot Fleet answer HTTP 200 even when
individual fleets could not be deleted, and the shared retry wrapper wraps the
final failure in ``MaxRetriesExceededError``. These helpers turn both shapes
into plain exceptions and structured alert records.
"""

from __future__ import annotations

from typing import Any, Callable, Literal

from botocore.exceptions import ClientError

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.resilience.exceptions import MaxRetriesExceededError
from orb.providers.aws.exceptions.aws_exceptions import AWSError, AWSInfrastructureError

FleetKind = Literal["ec2_fleet", "spot_fleet"]

# Error codes meaning the fleet is already gone.
_NOT_FOUND_CODES = {"fleetIdDoesNotExist", "fleetRequestIdDoesNotExist"}
# Error codes meaning the fleet is not in a state that allows the operation;
# acceptable only when a describe confirms it is already deleted / cancelled.
_NOT_IN_STATE_CODES = {"fleetNotInDeletableState", "fleetRequestNotInCancellableState"}

_UNSUCCESSFUL_KEY: dict[FleetKind, tuple[str, str]] = {
    "ec2_fleet": ("UnsuccessfulFleetDeletions", "FleetId"),
    "spot_fleet": ("UnsuccessfulFleetRequests", "SpotFleetRequestId"),
}


def unwrap_aws_error(exc: BaseException) -> BaseException:
    """Return the underlying error when *exc* is a retry-exhaustion wrapper."""
    if isinstance(exc, MaxRetriesExceededError) and exc.last_exception is not None:
        return exc.last_exception
    return exc


def error_code_of(exc: BaseException) -> str:
    """Return the AWS error code of *exc* (after unwrapping), or an empty string."""
    inner = unwrap_aws_error(exc)
    if isinstance(inner, ClientError):
        return str(inner.response.get("Error", {}).get("Code", ""))
    if isinstance(inner, AWSError):
        return str(inner.aws_error_code or "")
    return ""


def region_of(client: Any) -> str:
    """Return the region name of an AWS client wrapper, or an empty string."""
    region = getattr(client, "region_name", "")  # getattr: client wrappers may not expose a region
    return region if isinstance(region, str) else ""


def emit_alert(
    logger: LoggingPort,
    event: str,
    message: str,
    *,
    resource_type: str,
    resource_id: str,
    request_id: str = "",
    region: str = "",
    reason: str = "",
    level: Literal["error", "warning"] = "error",
) -> None:
    """Log one structured, alertable record with a stable event name.

    The request id and region are repeated in the message text so they survive
    plain-text log formatters that drop structured fields.
    """
    context = ", ".join(
        f"{key}={value}" for key, value in (("request_id", request_id), ("region", region)) if value
    )
    text = f"{message} [{context}]" if context else message
    extra = {
        "event": event,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "request_id": request_id,
        "region": region,
        "reason": reason,
    }
    if level == "warning":
        logger.warning("%s", text, extra=extra)
    else:
        logger.error("%s", text, extra=extra)


def check_unsuccessful_fleet_response(
    response: dict[str, Any],
    kind: FleetKind,
    is_gone: Callable[[str], bool],
) -> None:
    """Raise when a delete / cancel response reports unsuccessful entries.

    * ``fleetIdDoesNotExist`` / ``fleetRequestIdDoesNotExist`` count as success.
    * ``fleetNotInDeletableState`` / ``fleetRequestNotInCancellableState`` count
      as success only when ``is_gone(fleet_id)`` confirms the fleet is already
      deleted / cancelled.
    * Any other code (``unexpectedError``, malformed id, ...) raises. The retry
      wrapper only retries raised exceptions, not unsuccessful entries in a
      200 response, so these are not retried here; delete and cancel are
      idempotent and the caller can repeat them.

    Args:
        response: Raw API response.
        kind: Which response shape to inspect.
        is_gone: Describe-based check returning True when the fleet is deleted
            or cancelled. Should raise or return False when unknown.
    """
    key, id_field = _UNSUCCESSFUL_KEY[kind]
    failures: list[str] = []
    for entry in response.get(key) or []:
        fleet_id = str(entry.get(id_field, ""))
        error = entry.get("Error") or {}
        code = str(error.get("Code", ""))
        if code in _NOT_FOUND_CODES:
            continue
        if code in _NOT_IN_STATE_CODES:
            try:
                confirmed_gone = is_gone(fleet_id)
            except Exception:
                # Could not confirm the state, so the entry stays a failure.
                confirmed_gone = False
            if confirmed_gone:
                continue
        failures.append(f"{fleet_id}: {code or 'unknown'} {error.get('Message', '')}".strip())
    if failures:
        raise AWSInfrastructureError(
            f"{kind.replace('_', ' ')} delete was not successful: " + "; ".join(failures)
        )


def delete_asg_or_confirm_gone(
    autoscaling_client: Any,
    retry: Callable[..., Any],
    logger: LoggingPort,
    asg_name: str,
) -> None:
    """Delete an Auto Scaling Group; raise unless it is confirmed gone.

    The delete is retried by *retry* (``ScalingActivityInProgress`` and
    ``ResourceInUse`` are transient and covered by that retry). When the delete
    ultimately fails, ``describe_auto_scaling_groups`` is the authority on
    whether the group still exists: a missing group is reported by AWS as a
    generic validation error, so the error type cannot be used to detect it.
    """
    logger.info("Deleting ASG %s", asg_name)
    try:
        retry(
            autoscaling_client.delete_auto_scaling_group,
            operation_type="critical",
            AutoScalingGroupName=asg_name,
            ForceDelete=True,
        )
    except Exception as exc:
        cause = unwrap_aws_error(exc)
        try:
            groups = autoscaling_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[asg_name]
            ).get("AutoScalingGroups", [])
        except Exception as describe_exc:
            raise AWSInfrastructureError(
                f"Failed to delete ASG {asg_name} and could not verify it is gone: {cause}"
            ) from describe_exc
        if not groups or all(g.get("Status") == "Delete in progress" for g in groups):
            logger.info("ASG %s is already gone or being deleted", asg_name)
            return
        raise AWSInfrastructureError(f"Failed to delete ASG {asg_name}: {cause}") from exc
    logger.info("Successfully deleted ASG %s", asg_name)
