"""Cloud-side ground truth for the lifecycle suite.

A ``Cloud`` is a small state machine standing next to the system under test. It
holds state that scenario steps set, renders that state through the real
provider API shape, intercepts every mutating call with a scenario-controlled
response and keeps an append-only call log. It never computes what ORB should
report (ids, addresses, images, counts); the oracle derives those from the
scenario and from this state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Machine states in the oracle's vocabulary.
PENDING = "pending"
RUNNING = "running"
STOPPING = "shutting-down"
TERMINATED = "terminated"
IN_FLIGHT_ACQUIRE = frozenset({PENDING})
IN_FLIGHT_RETURN = frozenset({RUNNING, STOPPING})


@dataclass(frozen=True)
class CloudMachine:
    machine_id: str
    state: str
    request_id: Optional[str] = None
    private_ip: Optional[str] = None
    image_id: Optional[str] = None
    deletion_requested: bool = False


@dataclass
class CallRecord:
    """One mutating cloud call and the machines it killed."""

    seq: int
    operation: str
    params: dict[str, Any]
    killed: set[str] = field(default_factory=set)
    created: list[str] = field(default_factory=list)
    resource: Optional[str] = None


class Cloud:
    """Interface every provider fake implements."""

    call_log: list[CallRecord]

    def machines(self, request_id: Optional[str] = None) -> list[CloudMachine]:
        raise NotImplementedError

    def advance(self) -> None:
        """Let every pending transition (launch, termination lag) happen."""
        raise NotImplementedError

    def hold_pending(self, count: Optional[int] = None) -> None:
        """Keep the next ``count`` (default all) launched machines pending until ``advance``."""
        raise NotImplementedError

    def lag_termination(self) -> None:
        """Show terminated machines as shutting-down until ``advance``."""
        raise NotImplementedError

    def limit_capacity(self, machines: int) -> None:
        """Allow only ``machines`` more machines to launch."""
        raise NotImplementedError

    def interrupt(self, machine_ids: list[str]) -> None:
        """Take machines away from ORB the way the provider would."""
        raise NotImplementedError

    def fail_next_abort(self) -> None:
        """Make the next resource-level abort fail with a non-transient error."""
        raise NotImplementedError

    def fail_reads(self, failing: bool = True) -> None:
        """Make every describe call fail with a non-transient server error."""
        raise NotImplementedError

    def unknown_machine_id(self) -> str:
        """An id in the provider's format that no machine has."""
        raise NotImplementedError

    # Convenience over the call log -------------------------------------------------

    def calls(self, *operations: str) -> list[CallRecord]:
        return [c for c in self.call_log if c.operation in operations]

    def killed(self) -> set[str]:
        out: set[str] = set()
        for record in self.call_log:
            out |= record.killed
        return out

    def count(self, request_id: str, *states: str) -> int:
        return sum(1 for m in self.machines(request_id) if m.state in states)
