"""Terminate EC2 instances while tolerating ids that no longer exist.

EC2 ``TerminateInstances`` is all-or-nothing: when any id in the batch is
unknown it answers ``InvalidInstanceID.NotFound`` and terminates none of the
others. An unknown id means the instance is already gone, which is the state a
terminate call is asking for, so it is reported separately instead of failing
the whole call.
"""

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from botocore.exceptions import ClientError

INSTANCE_NOT_FOUND = "InvalidInstanceID.NotFound"

_INSTANCE_ID_TOKEN = re.compile(r"(?<![\w-])i-[A-Za-z0-9]+(?![\w-])")


def instance_ids_in_message(message: str) -> set[str]:
    """Return the instance id tokens named in an EC2 error message."""
    return set(_INSTANCE_ID_TOKEN.findall(message))


def is_instance_not_found(error: BaseException) -> bool:
    """Return True when *error* is an ``InvalidInstanceID.NotFound`` client error."""
    return (
        isinstance(error, ClientError)
        and error.response.get("Error", {}).get("Code") == INSTANCE_NOT_FOUND
    )


def terminate_skipping_missing(
    terminate: Callable[[list[str]], dict[str, Any]],
    instance_ids: list[str],
) -> dict[str, Any]:
    """Terminate *instance_ids*, treating ids EC2 no longer knows as already gone.

    Args:
        terminate: Performs one TerminateInstances call for the given ids and
            returns the response. It raises ``ClientError`` on failure.
        instance_ids: Instances to terminate.

    Returns:
        ``{"TerminatingInstances": [...], "already_terminated": [...]}`` where
        ``already_terminated`` lists the ids EC2 reported as not found.

    Raises:
        ClientError: Any failure other than ``InvalidInstanceID.NotFound``.
    """
    remaining = list(dict.fromkeys(instance_ids))
    terminating: list[dict[str, Any]] = []
    already_terminated: list[str] = []

    while remaining:
        try:
            response = terminate(remaining)
        except ClientError as error:
            if not is_instance_not_found(error):
                raise
            message = str(error.response.get("Error", {}).get("Message", ""))
            named = instance_ids_in_message(message)
            unknown = [i for i in remaining if i in named]
            if not unknown and len(remaining) == 1:
                unknown = list(remaining)
            if not unknown:
                # The message names no known id: settle each instance on its own.
                for instance_id in remaining:
                    try:
                        single = terminate([instance_id])
                    except ClientError as single_error:
                        if not is_instance_not_found(single_error):
                            raise
                        already_terminated.append(instance_id)
                    else:
                        terminating.extend(single.get("TerminatingInstances", []))
                break
            already_terminated.extend(unknown)
            remaining = [i for i in remaining if i not in unknown]
        else:
            terminating.extend(response.get("TerminatingInstances", []))
            break

    return {"TerminatingInstances": terminating, "already_terminated": already_terminated}


# EC2 can answer NotFound for an instance for a few seconds after RunInstances.
RECENT_LAUNCH_SECONDS = 60
VISIBILITY_ATTEMPTS = 5
VISIBILITY_DELAY_SECONDS = 2.0


def recently_launched_ids(
    coordinates: Mapping[str, Mapping[str, Any]],
    now: datetime | None = None,
    max_age_seconds: float = RECENT_LAUNCH_SECONDS,
) -> list[str]:
    """Return ids of machines launched less than *max_age_seconds* ago."""
    now = now or datetime.now(timezone.utc)
    young: list[str] = []
    for machine_id, record in coordinates.items():
        launched = record.get("launched_at")
        if not launched:
            continue
        try:
            moment = datetime.fromisoformat(str(launched))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        if (now - moment).total_seconds() < max_age_seconds:
            young.append(machine_id)
    return young


def wait_until_visible(
    ec2_client: Any,
    instance_ids: list[str],
    attempts: int = VISIBILITY_ATTEMPTS,
    delay: float = VISIBILITY_DELAY_SECONDS,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Give just-launched instances a bounded window to become visible to EC2.

    Returns as soon as every id is known, or after *attempts* tries; the caller
    then terminates and treats any id still unknown as already gone.
    """
    wait = sleep or time.sleep
    pending = list(instance_ids)
    for attempt in range(attempts):
        try:
            ec2_client.describe_instances(InstanceIds=pending)
            return
        except ClientError as error:
            if not is_instance_not_found(error):
                return
            named = instance_ids_in_message(str(error.response.get("Error", {}).get("Message", "")))
            still_unknown = [i for i in pending if i in named]
            pending = still_unknown or pending
        if attempt < attempts - 1:
            wait(delay)
