"""Re-export shim — FollowUpContext types live in the domain layer.

Import from ``orb.domain.base.follow_up_context`` directly for new code.
This module exists only for backward compatibility.
"""

from orb.domain.base.follow_up_context import (
    DeploymentPollingFollowUpContext,
    FollowUpContext,
    TerminationFollowUpContext,
)

__all__ = [
    "DeploymentPollingFollowUpContext",
    "FollowUpContext",
    "TerminationFollowUpContext",
]
