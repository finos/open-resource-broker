"""Typed follow-up context for provider operations that require background tracking.

``FollowUpContext`` describes *what* kind of follow-up is needed after a provider
operation returns ``RequiresFollowUp``.  These are pure value objects — frozen
dataclasses with literal discriminants and primitive fields — and therefore
belong in the domain layer alongside ``OperationOutcome``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


# ---------------------------------------------------------------------------
# Individual context variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TerminationFollowUpContext:
    """Context for a return-machines follow-up.

    The provider accepted the termination request but instances are still in
    ``shutting-down`` state.  The background poller should wait until all
    ``pending_instance_ids`` reach ``terminated`` before closing the request.

    Attributes:
        follow_up_kind: Discriminator tag (always ``"termination"``).
        pending_instance_ids: Instance IDs still being terminated.
        expected_terminal_state: State that signals completion (``"terminated"``
            for EC2, ``"deleted"`` for other providers).
        poll_after: Earliest wall-clock time to issue the next status check.
        provider_handle: Optional provider-side tracking ID (e.g. a batch job
            reference); may be ``None`` for simple TerminateInstances calls.
    """

    follow_up_kind: Literal["termination"] = field(default="termination", init=False)
    pending_instance_ids: list[str] = field(default_factory=list)
    expected_terminal_state: str = "terminated"
    poll_after: datetime | None = None
    provider_handle: str | None = None


@dataclass(frozen=True)
class DeploymentPollingFollowUpContext:
    """Context for an acquire follow-up where the fleet is still initialising.

    The provider accepted the launch request.  The background poller should
    check the fleet/spot-request until all instances are ``running`` or a
    terminal failure state is reached.

    Attributes:
        follow_up_kind: Discriminator tag (always ``"deployment_polling"``).
        pending_resource_ids: Fleet/request IDs still being fulfilled.
        expected_terminal_state: State that signals successful completion
            (default ``"running"``).
        poll_after: Earliest wall-clock time to issue the next status check.
        provider_handle: Provider-side tracking ID (fleet request ID, etc.).
    """

    follow_up_kind: Literal["deployment_polling"] = field(
        default="deployment_polling", init=False
    )
    pending_resource_ids: list[str] = field(default_factory=list)
    expected_terminal_state: str = "running"
    poll_after: datetime | None = None
    provider_handle: str | None = None


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

FollowUpContext = TerminationFollowUpContext | DeploymentPollingFollowUpContext
