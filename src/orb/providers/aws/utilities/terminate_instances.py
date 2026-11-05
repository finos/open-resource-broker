"""Terminate EC2 instances while tolerating ids that no longer exist.

EC2 ``TerminateInstances`` is all-or-nothing: when any id in the batch is
unknown it answers ``InvalidInstanceID.NotFound`` and terminates none of the
others. An unknown id means the instance is already gone, which is the state a
terminate call is asking for, so it is reported separately instead of failing
the whole call.
"""

from typing import Any, Callable

from botocore.exceptions import ClientError

INSTANCE_NOT_FOUND = "InvalidInstanceID.NotFound"


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
            unknown = [i for i in remaining if i in message]
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
