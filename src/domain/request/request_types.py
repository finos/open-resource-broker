"""Request type and status enumerations."""

from __future__ import annotations

from enum import Enum


class RequestType(str, Enum):
    """
    Type of request - Business-focused enumeration.

    This represents what the user wants to do from a business perspective.

    Attributes:
        ACQUIRE: Request to acquire new machines
        RETURN: Request to return existing machines
    """

    ACQUIRE = "acquire"
    RETURN = "return"

    @classmethod
    def from_str(cls, value: str) -> "RequestType":
        """
        Create RequestType from string value.

        Args:
            value: String value to convert

        Returns:
            RequestType enum value

        Raises:
            ValueError: If value is not a valid RequestType
        """
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(
                f"Invalid RequestType: {value}. Valid values: {[e.value for e in cls]}"
            )

    def to_operation_type(self) -> str:
        """
        Map business request type to technical operation type.

        This provides the mapping between business concepts and technical operations
        for AWS provider operations.

        Returns:
            Technical operation type string
        """
        mapping = {RequestType.ACQUIRE: "provision", RequestType.RETURN: "terminate"}
        return mapping[self]

    def is_acquire(self) -> bool:
        """Check if this is an acquire request."""
        return self == RequestType.ACQUIRE

    def is_return(self) -> bool:
        """Check if this is a return request."""
        return self == RequestType.RETURN


class RequestStatus(str, Enum):
    """
    Consolidated request status enumeration - Complete lifecycle coverage.

    This represents the current state of the request in its lifecycle,
    combining both processing states and outcome states.

    Attributes:
        PENDING: Request has been created but not yet processed
        IN_PROGRESS: Request is currently being processed
        COMPLETED: Request has been successfully completed
        FAILED: Request has failed and cannot be completed
        CANCELLED: Request has been cancelled by user or system
        PARTIAL: Request completed with partial success
        TIMEOUT: Request has timed out
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"
    TIMEOUT = "timeout"

    @classmethod
    def from_str(cls, value: str) -> "RequestStatus":
        """
        Create RequestStatus from string value.

        Args:
            value: String value to convert

        Returns:
            RequestStatus enum value

        Raises:
            ValueError: If value is not a valid RequestStatus
        """
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(
                f"Invalid RequestStatus: {value}. Valid values: {[e.value for e in cls]}"
            )

    def is_terminal(self) -> bool:
        """Check if this status represents a terminal state."""
        return self in [
            RequestStatus.COMPLETED,
            RequestStatus.FAILED,
            RequestStatus.CANCELLED,
            RequestStatus.TIMEOUT,
        ]

    def is_active(self) -> bool:
        """Check if this status represents an active state."""
        return self in [RequestStatus.PENDING, RequestStatus.IN_PROGRESS]

    def can_transition_to(self, new_status: "RequestStatus") -> bool:
        """
        Check if transition to new status is valid.

        Args:
            new_status: Target status to transition to

        Returns:
            True if transition is valid, False otherwise
        """
        valid_transitions = {
            RequestStatus.PENDING: [RequestStatus.IN_PROGRESS, RequestStatus.CANCELLED],
            RequestStatus.IN_PROGRESS: [
                RequestStatus.COMPLETED,
                RequestStatus.FAILED,
                RequestStatus.CANCELLED,
                RequestStatus.TIMEOUT,
            ],
            RequestStatus.COMPLETED: [],  # Terminal state
            RequestStatus.FAILED: [],  # Terminal state
            RequestStatus.CANCELLED: [],  # Terminal state
            RequestStatus.TIMEOUT: [],  # Terminal state
        }

        return new_status in valid_transitions.get(self, [])


class MachineResult(str, Enum):
    """
    Result status for individual machines within a request.

    This represents the outcome for each machine in a multi-machine request.

    Attributes:
        SUCCESS: Machine was successfully provisioned/terminated
        FAILED: Machine operation failed
        PENDING: Machine operation is still pending
        SKIPPED: Machine operation was skipped
    """

    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"
    SKIPPED = "skipped"

    @classmethod
    def from_str(cls, value: str) -> "MachineResult":
        """
        Create MachineResult from string value.

        Args:
            value: String value to convert

        Returns:
            MachineResult enum value

        Raises:
            ValueError: If value is not a valid MachineResult
        """
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(
                f"Invalid MachineResult: {value}. Valid values: {[e.value for e in cls]}"
            )

    def is_terminal(self) -> bool:
        """Check if this result represents a terminal state."""
        return self in [
            MachineResult.SUCCESS,
            MachineResult.FAILED,
            MachineResult.SKIPPED,
        ]

    def is_successful(self) -> bool:
        """Check if this result represents a successful outcome."""
        return self == MachineResult.SUCCESS
