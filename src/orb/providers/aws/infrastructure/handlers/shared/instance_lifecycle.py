"""Classification of instance end-of-life states for fulfilment verdicts.

An instance that is shutting down or terminated is classified from its EC2
state reason:

* reason shows a requested shutdown -> intentional teardown;
* any other reason (launch error, spot interruption, ...) -> launch failure;
* no reason -> unknown. Callers treat unknown endings as intentional once every
  requested instance exists (the target was reached, so this is a later
  teardown), and as launch failures while fewer instances exist than were
  requested, so a shortfall is never reported as fulfilled.
"""

from __future__ import annotations

from typing import Any

# Instance states that mean the instance is being (or has been) taken away.
TEARDOWN_STATUSES = ("shutting-down", "terminated", "stopping", "stopped")

_ENDING_STATUSES = ("shutting-down", "terminated")

# EC2 state-reason prefixes for terminations that someone asked for. Any other
# reason on an ending instance (Client.InternalError, Client.VolumeLimitExceeded,
# Server.InsufficientInstanceCapacity, Server.SpotInstanceTermination, ...) means
# AWS ended it, which is a launch failure from the request's point of view.
_INTENTIONAL_REASON_PREFIXES = (
    "Client.UserInitiatedShutdown",
    "Client.InstanceInitiatedShutdown",
    "Client.UserInitiatedHibernate",
    "User initiated",
)


def is_unknown_ending(inst: dict[str, Any]) -> bool:
    """Return True for an ending instance that carries no state reason.

    Whether such an instance was returned on purpose or ended by AWS cannot be
    told apart. Fulfilment treats it as intentional once every requested
    instance exists; otherwise it is conservatively counted as a launch
    failure, so a shortfall is never reported as fulfilled.
    """
    return inst.get("status") in _ENDING_STATUSES and not inst.get("status_reason")


def is_launch_failure(inst: dict[str, Any], *, use_state_reason: bool = True) -> bool:
    """Return True when the instance ended or failed without being asked to.

    ``failed`` status is always a launch failure. An ending instance
    (shutting-down / terminated) is a launch failure when its state reason says
    AWS ended it. An ending instance with no reason is *not* reported here; use
    :func:`is_unknown_ending` and decide per request.
    """
    status = inst.get("status")
    if status == "failed":
        return True
    if use_state_reason and status in _ENDING_STATUSES:
        reason = inst.get("status_reason")
        if reason and not str(reason).startswith(_INTENTIONAL_REASON_PREFIXES):
            return True
    return False


def is_intentional_teardown(inst: dict[str, Any], *, use_state_reason: bool = True) -> bool:
    """Return True for teardown states that are known not to be launch failures.

    Stopping/stopped are always intentional. Ending instances count only when
    their state reason shows the shutdown was requested; unknown endings are
    excluded (see :func:`is_unknown_ending`).
    """
    status = inst.get("status")
    if status in ("stopping", "stopped"):
        return True
    return (
        status in _ENDING_STATUSES
        and not is_launch_failure(inst, use_state_reason=use_state_reason)
        and not is_unknown_ending(inst)
    )
