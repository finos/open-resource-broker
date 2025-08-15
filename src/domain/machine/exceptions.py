"""Machine domain exceptions."""

from domain.base.exceptions import (
    DomainException,
    EntityNotFoundError,
    ValidationError,
)


class MachineException(DomainException):
    """Base exception for machine domain errors."""


class MachineNotFoundError(EntityNotFoundError):
    """Raised when a machine is not found."""

    def __init__(self, machine_id: str):
        """Initialize the instance."""
        super().__init__("Machine", machine_id)


class MachineValidationError(ValidationError):
    """Raised when machine validation fails."""


class InvalidMachineStateError(MachineException):
    """Raised when attempting an invalid state transition."""

    def __init__(self, current_state: str, attempted_state: str):
        """Initialize machine state transition error with states."""
        message = f"Cannot transition from {current_state} to {attempted_state}"
        super().__init__(
            message,
            "INVALID_STATE_TRANSITION",
            {"current_state": current_state, "attempted_state": attempted_state},
        )


class MachineProvisioningError(MachineException):
    """Raised when machine provisioning fails."""
