"""Domain-specific exceptions - business rule violations and domain errors."""

from typing import Any, Optional

# =============================================================================
# BASE DOMAIN EXCEPTIONS
# =============================================================================


class DomainException(Exception):
    """Base exception for all domain-level errors."""

    def __init__(self, message: str, details: Optional[dict[str, Any]] = None) -> None:
        """Initialize domain exception.

        Args:
            message: Human-readable error message
            details: Additional context about the error
        """
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        """String representation of the exception."""
        if self.details:
            details_str = ", ".join(f"{k}={v}" for k, v in self.details.items())
            return f"{self.message} ({details_str})"
        return self.message


class BusinessRuleViolation(DomainException):
    """Exception raised when a business rule is violated."""

    def __init__(self, rule: str, message: str, details: Optional[dict[str, Any]] = None) -> None:
        """Initialize business rule violation.

        Args:
            rule: Name of the violated business rule
            message: Description of the violation
            details: Additional context
        """
        super().__init__(message, details)
        self.rule = rule


class AggregateInvariantViolation(DomainException):
    """Exception raised when an aggregate invariant is violated."""

    def __init__(
        self,
        aggregate_type: str,
        invariant: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Initialize aggregate invariant violation.

        Args:
            aggregate_type: Type of aggregate (e.g., 'Request', 'Machine')
            invariant: Name of the violated invariant
            message: Description of the violation
            details: Additional context
        """
        super().__init__(message, details)
        self.aggregate_type = aggregate_type
        self.invariant = invariant


class ValueObjectValidationError(DomainException):
    """Exception raised when value object validation fails."""

    def __init__(self, value_object_type: str, field: str, value: Any, reason: str) -> None:
        """Initialize value object validation error.

        Args:
            value_object_type: Type of value object
            field: Field that failed validation
            value: Invalid value
            reason: Why validation failed
        """
        message = f"Invalid {field} for {value_object_type}: {reason}"
        details = {"field": field, "value": value, "reason": reason}
        super().__init__(message, details)
        self.value_object_type = value_object_type
        self.field = field
        self.value = value
        self.reason = reason


# =============================================================================
# TEMPLATE DOMAIN EXCEPTIONS
# =============================================================================


class TemplateException(DomainException):
    """Base exception for template-related errors."""

    pass


class TemplateNotFoundError(TemplateException):
    """Exception raised when a template is not found."""

    def __init__(self, template_id: str) -> None:
        """Initialize template not found error.

        Args:
            template_id: ID of the template that was not found
        """
        super().__init__(f"Template not found: {template_id}", {"template_id": template_id})
        self.template_id = template_id


class TemplateValidationError(TemplateException):
    """Exception raised when template validation fails."""

    def __init__(
        self, template_id: str, reason: str, details: Optional[dict[str, Any]] = None
    ) -> None:
        """Initialize template validation error.

        Args:
            template_id: ID of the invalid template
            reason: Why validation failed
            details: Additional context
        """
        super().__init__(
            f"Template validation failed for {template_id}: {reason}",
            {"template_id": template_id, "reason": reason, **(details or {})},
        )
        self.template_id = template_id
        self.reason = reason


class TemplateConfigurationError(TemplateException):
    """Exception raised when template configuration is invalid."""

    def __init__(self, template_id: str, field: str, reason: str) -> None:
        """Initialize template configuration error.

        Args:
            template_id: ID of the template
            field: Configuration field that is invalid
            reason: Why the configuration is invalid
        """
        super().__init__(
            f"Invalid template configuration for {template_id}.{field}: {reason}",
            {"template_id": template_id, "field": field, "reason": reason},
        )
        self.template_id = template_id
        self.field = field
        self.reason = reason


# =============================================================================
# REQUEST DOMAIN EXCEPTIONS
# =============================================================================


class RequestException(DomainException):
    """Base exception for request-related errors."""

    pass


class RequestNotFoundError(RequestException):
    """Exception raised when a request is not found."""

    def __init__(self, request_id: str) -> None:
        """Initialize request not found error.

        Args:
            request_id: ID of the request that was not found
        """
        super().__init__(f"Request not found: {request_id}", {"request_id": request_id})
        self.request_id = request_id


class InvalidRequestStateTransition(RequestException):
    """Exception raised when an invalid state transition is attempted."""

    def __init__(self, request_id: str, from_status: str, to_status: str, reason: str) -> None:
        """Initialize invalid state transition error.

        Args:
            request_id: ID of the request
            from_status: Current status
            to_status: Attempted new status
            reason: Why the transition is invalid
        """
        super().__init__(
            f"Invalid state transition for request {request_id}: {from_status} -> {to_status}. {reason}",
            {
                "request_id": request_id,
                "from_status": from_status,
                "to_status": to_status,
                "reason": reason,
            },
        )
        self.request_id = request_id
        self.from_status = from_status
        self.to_status = to_status


class RequestValidationError(RequestException):
    """Exception raised when request validation fails."""

    def __init__(
        self, request_id: str, reason: str, details: Optional[dict[str, Any]] = None
    ) -> None:
        """Initialize request validation error.

        Args:
            request_id: ID of the invalid request
            reason: Why validation failed
            details: Additional context
        """
        super().__init__(
            f"Request validation failed for {request_id}: {reason}",
            {"request_id": request_id, "reason": reason, **(details or {})},
        )
        self.request_id = request_id
        self.reason = reason


class RequestCapacityExceeded(RequestException):
    """Exception raised when request exceeds capacity limits."""

    def __init__(self, request_id: str, requested: int, available: int) -> None:
        """Initialize capacity exceeded error.

        Args:
            request_id: ID of the request
            requested: Number of instances requested
            available: Number of instances available
        """
        super().__init__(
            f"Request {request_id} exceeds capacity: requested {requested}, available {available}",
            {"request_id": request_id, "requested": requested, "available": available},
        )
        self.request_id = request_id
        self.requested = requested
        self.available = available


# =============================================================================
# MACHINE DOMAIN EXCEPTIONS
# =============================================================================


class MachineException(DomainException):
    """Base exception for machine-related errors."""

    pass


class MachineNotFoundError(MachineException):
    """Exception raised when a machine is not found."""

    def __init__(self, machine_id: str) -> None:
        """Initialize machine not found error.

        Args:
            machine_id: ID of the machine that was not found
        """
        super().__init__(f"Machine not found: {machine_id}", {"machine_id": machine_id})
        self.machine_id = machine_id


class InvalidMachineStateTransition(MachineException):
    """Exception raised when an invalid machine state transition is attempted."""

    def __init__(self, machine_id: str, from_status: str, to_status: str, reason: str) -> None:
        """Initialize invalid state transition error.

        Args:
            machine_id: ID of the machine
            from_status: Current status
            to_status: Attempted new status
            reason: Why the transition is invalid
        """
        super().__init__(
            f"Invalid state transition for machine {machine_id}: {from_status} -> {to_status}. {reason}",
            {
                "machine_id": machine_id,
                "from_status": from_status,
                "to_status": to_status,
                "reason": reason,
            },
        )
        self.machine_id = machine_id
        self.from_status = from_status
        self.to_status = to_status


class MachineValidationError(MachineException):
    """Exception raised when machine validation fails."""

    def __init__(
        self, machine_id: str, reason: str, details: Optional[dict[str, Any]] = None
    ) -> None:
        """Initialize machine validation error.

        Args:
            machine_id: ID of the invalid machine
            reason: Why validation failed
            details: Additional context
        """
        super().__init__(
            f"Machine validation failed for {machine_id}: {reason}",
            {"machine_id": machine_id, "reason": reason, **(details or {})},
        )
        self.machine_id = machine_id
        self.reason = reason


# =============================================================================
# PROVIDER DOMAIN EXCEPTIONS
# =============================================================================


class ProviderException(DomainException):
    """Base exception for provider-related errors."""

    pass


class ProviderNotFoundError(ProviderException):
    """Exception raised when a provider is not found."""

    def __init__(self, provider_name: str, provider_type: Optional[str] = None) -> None:
        """Initialize provider not found error.

        Args:
            provider_name: Name of the provider
            provider_type: Type of provider (optional)
        """
        message = f"Provider not found: {provider_name}"
        if provider_type:
            message += f" (type: {provider_type})"
        super().__init__(message, {"provider_name": provider_name, "provider_type": provider_type})
        self.provider_name = provider_name
        self.provider_type = provider_type


class ProviderNotAvailableError(ProviderException):
    """Exception raised when a provider is not available."""

    def __init__(self, provider_name: str, reason: str) -> None:
        """Initialize provider not available error.

        Args:
            provider_name: Name of the provider
            reason: Why the provider is not available
        """
        super().__init__(
            f"Provider {provider_name} is not available: {reason}",
            {"provider_name": provider_name, "reason": reason},
        )
        self.provider_name = provider_name
        self.reason = reason


class ProviderValidationError(ProviderException):
    """Exception raised when provider validation fails."""

    def __init__(
        self, provider_name: str, reason: str, details: Optional[dict[str, Any]] = None
    ) -> None:
        """Initialize provider validation error.

        Args:
            provider_name: Name of the provider
            reason: Why validation failed
            details: Additional context
        """
        super().__init__(
            f"Provider validation failed for {provider_name}: {reason}",
            {"provider_name": provider_name, "reason": reason, **(details or {})},
        )
        self.provider_name = provider_name
        self.reason = reason


# =============================================================================
# RESOURCE DOMAIN EXCEPTIONS
# =============================================================================


class ResourceException(DomainException):
    """Base exception for resource-related errors."""

    pass


class ResourceQuotaExceeded(ResourceException):
    """Exception raised when resource quota is exceeded."""

    def __init__(self, resource_type: str, requested: int, limit: int, used: int) -> None:
        """Initialize quota exceeded error.

        Args:
            resource_type: Type of resource
            requested: Amount requested
            limit: Quota limit
            used: Currently used amount
        """
        available = limit - used
        super().__init__(
            f"Resource quota exceeded for {resource_type}: requested {requested}, available {available} (limit: {limit}, used: {used})",
            {
                "resource_type": resource_type,
                "requested": requested,
                "limit": limit,
                "used": used,
                "available": available,
            },
        )
        self.resource_type = resource_type
        self.requested = requested
        self.limit = limit
        self.used = used
        self.available = available


class ResourceNotFoundError(ResourceException):
    """Exception raised when a resource is not found."""

    def __init__(self, resource_type: str, resource_id: str) -> None:
        """Initialize resource not found error.

        Args:
            resource_type: Type of resource
            resource_id: ID of the resource
        """
        super().__init__(
            f"{resource_type} not found: {resource_id}",
            {"resource_type": resource_type, "resource_id": resource_id},
        )
        self.resource_type = resource_type
        self.resource_id = resource_id


class ResourceConflictError(ResourceException):
    """Exception raised when there's a resource conflict."""

    def __init__(self, resource_type: str, resource_id: str, reason: str) -> None:
        """Initialize resource conflict error.

        Args:
            resource_type: Type of resource
            resource_id: ID of the resource
            reason: Why there's a conflict
        """
        super().__init__(
            f"Resource conflict for {resource_type} {resource_id}: {reason}",
            {"resource_type": resource_type, "resource_id": resource_id, "reason": reason},
        )
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.reason = reason
