"""Typed discriminated union for provider operation outcomes.

This module defines the ``OperationOutcome`` type — a pure domain concept that
replaces stringly-typed ``provider_metadata`` keys with explicit, exhaustively-
matchable variants.  All variants are frozen dataclasses so they are
value-comparable and safe to store in caches or queues.

Usage example (exhaustive match with ``assert_never``)::

    from typing import assert_never
    from orb.domain.base.operation_outcome import (
        Accepted, Completed, Failed, OperationOutcome, RequiresFollowUp,
    )

    def handle(outcome: OperationOutcome) -> str:
        match outcome:
            case Accepted(request_id=rid):
                return f"accepted, tracking {rid}"
            case Completed(resource_ids=ids):
                return f"done: {ids}"
            case RequiresFollowUp(context=ctx):
                return f"follow-up needed: {ctx.follow_up_kind}"
            case Failed(error=msg):
                return f"failed: {msg}"
            case _ as unreachable:
                assert_never(unreachable)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from orb.domain.base.follow_up_context import FollowUpContext


@dataclass(frozen=True)
class Accepted:
    """The provider accepted the request and is processing it asynchronously.

    This is the normal AWS outcome for ``acquire`` and ``return_machines``:
    the API call succeeded but instances are ``pending`` / ``shutting-down``.
    Callers must poll via ``get_status`` until a terminal outcome is reached.

    Attributes:
        request_id: Provider-side tracking identifier (e.g. EC2Fleet request ID).
        pending_resource_ids: Resource/instance IDs in a non-terminal state.
        metadata: Optional provider-specific supplementary data.
    """

    request_id: str
    pending_resource_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Completed:
    """The operation reached a terminal successful state.

    All requested resources exist and are in their expected final state
    (``running`` for acquire, ``terminated`` for return).

    Attributes:
        resource_ids: IDs of the resources in their terminal state.
        metadata: Optional provider-specific supplementary data.
    """

    resource_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequiresFollowUp:
    """The provider acknowledged the request but a background follow-up is needed.

    Used when the operation cannot be completed in a single synchronous call and
    requires a specific follow-up action beyond simple polling (e.g. a webhook
    callback, a secondary API call, or a domain-side state machine transition).

    Attributes:
        context: Typed descriptor of *what* follow-up is required and *how*.
        metadata: Optional provider-specific supplementary data.
    """

    context: FollowUpContext
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Failed:
    """The operation failed either permanently or transiently.

    Attributes:
        error: Human-readable error description.
        recoverable: ``True`` if a retry may succeed (e.g. throttle); ``False``
            for hard failures (e.g. invalid configuration).
        metadata: Optional provider-specific supplementary data.
    """

    error: str
    recoverable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# Discriminated union — use ``match`` / ``isinstance`` for exhaustive dispatch.
OperationOutcome = Accepted | Completed | RequiresFollowUp | Failed
