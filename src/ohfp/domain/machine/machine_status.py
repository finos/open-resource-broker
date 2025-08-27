"""Machine status enumerations."""

from __future__ import annotations

from enum import Enum


class MachineStatus(str, Enum):
    """
    Machine status with mapping to HostFactory states.
    HostFactory expects machine states:
    - running
    - stopped
    - terminated
    - shutting-down
    - stopping
    """

    # Define with default values
    # External states (HostFactory-facing)
    PENDING = "pending"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    SHUTTING_DOWN = "shutting-down"
    TERMINATED = "terminated"

    # Internal states (for tracking)
    RETURNED = "returned"  # Used for return requests
    FAILED = "failed"  # Used for failed provisioning
    UNKNOWN = "unknown"  # Used for unrecognized states

    @classmethod
    def from_str(cls, value: str) -> MachineStatus:
        """Create MachineStatus from string value.

        Args:
            value: Status string value

        Returns:
            MachineStatus instance

        Raises:
            ValueError: If value is not a valid status
        """
        # Direct mapping for domain values
        status_map = {
            "pending": cls.PENDING,
            "running": cls.RUNNING,
            "stopping": cls.STOPPING,
            "stopped": cls.STOPPED,
            "shutting-down": cls.SHUTTING_DOWN,
            "terminated": cls.TERMINATED,
            "returned": cls.RETURNED,
            "failed": cls.FAILED,
            "unknown": cls.UNKNOWN,
        }

        normalized_value = value.lower().replace("_", "-")
        if normalized_value in status_map:
            return status_map[normalized_value]

        raise ValueError(f"Invalid machine status: {value}")

    def can_transition_to(self, new_status: MachineStatus) -> bool:
        """Validate state transition."""
        valid_transitions = {
            self.PENDING: {self.RUNNING, self.FAILED},
            self.RUNNING: {self.STOPPING, self.SHUTTING_DOWN},
            self.STOPPING: {self.STOPPED, self.FAILED},
            self.STOPPED: {self.RUNNING, self.TERMINATED},
            self.SHUTTING_DOWN: {self.TERMINATED},
            self.TERMINATED: {self.RETURNED},
            self.FAILED: set(),  # Terminal state
            self.RETURNED: set(),  # Terminal state
            self.UNKNOWN: {self.PENDING, self.RUNNING, self.STOPPED, self.TERMINATED},
        }
        return new_status in valid_transitions.get(self, set())

    @property
    def is_terminal(self) -> bool:
        """Check if status is terminal."""
        return self in {self.TERMINATED, self.FAILED, self.RETURNED}

    @property
    def is_active(self) -> bool:
        """Check if status is active."""
        return self in {self.PENDING, self.RUNNING}
