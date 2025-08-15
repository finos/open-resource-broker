"""AWS-specific exceptions."""

from typing import Any, Dict, Optional

from domain.base.exceptions import InfrastructureError


class AWSError(InfrastructureError):
    """Base class for AWS-related errors."""

    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ) -> None:
        """Initialize AWS exception.

        Args:
            message: Human-readable error message
            details: Additional error details and context
            error_code: Specific error code for programmatic handling
        """
        super().__init__(message, error_code or self.__class__.__name__, details)
        self.error_code = error_code or self.__class__.__name__

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary."""
        result = super().to_dict()
        if self.error_code and self.error_code != self.__class__.__name__:
            result["error_code"] = self.error_code
        return result


class AWSValidationError(AWSError):
    """Raised when AWS resource validation fails."""


class AWSEntityNotFoundError(AWSError):
    """Raised when an AWS resource is not found."""


class QuotaExceededError(AWSError):
    """Raised when AWS service quotas would be exceeded."""


class ResourceInUseError(AWSError):
    """Raised when an AWS resource is already in use."""


class AuthorizationError(AWSError):
    """Raised when there are insufficient permissions."""


class RateLimitError(AWSError):
    """Raised when AWS API rate limits are exceeded."""


class NetworkError(AWSError):
    """Raised when there are network-related issues."""


class AWSInfrastructureError(AWSError):
    """Raised for general AWS infrastructure errors."""


class AWSConfigurationError(AWSError):
    """Raised when AWS configuration is invalid."""


class ResourceStateError(AWSError):
    """Raised when a resource is in an invalid state for the operation."""

    def __init__(
        self,
        message: str,
        resource_id: str,
        current_state: str,
        expected_states: list[str],
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize resource state error.

        Args:
            message: Human-readable error message
            resource_id: ID of the resource in invalid state
            current_state: Current state of the resource
            expected_states: List of valid states for the operation
            details: Additional error context
        """
        super().__init__(
            message,
            details={
                "resource_id": resource_id,
                "current_state": current_state,
                "expected_states": expected_states,
                **(details or {}),
            },
        )
        self.resource_id = resource_id
        self.current_state = current_state
        self.expected_states = expected_states


class TaggingError(AWSError):
    """Raised when there are issues with AWS resource tagging."""

    def __init__(
        self,
        message: str,
        resource_id: str,
        tags: Dict[str, str],
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize resource tagging error.

        Args:
            message: Human-readable error message
            resource_id: ID of the resource that failed to tag
            tags: Tags that failed to be applied
            details: Additional error context
        """
        super().__init__(
            message,
            details={"resource_id": resource_id, "tags": tags, **(details or {})},
        )
        self.resource_id = resource_id
        self.tags = tags


class LaunchError(AWSError):
    """Raised when there are issues launching AWS resources."""

    def __init__(
        self,
        message: str,
        template_id: str,
        launch_params: Dict[str, Any],
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize the instance."""
        super().__init__(
            message,
            details={
                "template_id": template_id,
                "launch_params": launch_params,
                **(details or {}),
            },
        )
        self.template_id = template_id
        self.launch_params = launch_params


class TerminationError(AWSError):
    """Raised when there are issues terminating AWS resources."""

    def __init__(
        self,
        message: str,
        resource_ids: list[str],
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, details={"resource_ids": resource_ids, **(details or {})})
        self.resource_ids = resource_ids


class EC2InstanceNotFoundError(AWSEntityNotFoundError):
    """Raised when an EC2 instance cannot be found."""

    def __init__(self, instance_id: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(
            f"EC2 instance not found: {instance_id}",
            details={"instance_id": instance_id, **(details or {})},
        )
        self.instance_id = instance_id


class ResourceCleanupError(AWSError):
    """Raised when there are issues cleaning up AWS resources."""

    def __init__(
        self,
        message: str,
        resource_id: str,
        resource_type: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            details={
                "resource_id": resource_id,
                "resource_type": resource_type,
                **(details or {}),
            },
        )
        self.resource_id = resource_id
        self.resource_type = resource_type


class LaunchTemplateError(LaunchError):
    """Raised when there are issues with launch templates."""

    def __init__(
        self,
        message: str,
        template_id: str,
        operation: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, template_id, {"operation": operation}, details)
        self.operation = operation


class FleetRequestError(LaunchError):
    """Raised when there are issues with fleet requests."""

    def __init__(
        self,
        message: str,
        fleet_type: str,
        request_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, fleet_type, {"request_id": request_id}, details)
        self.fleet_type = fleet_type
        self.request_id = request_id


class AMIValidationError(AWSValidationError):
    """Raised when there are issues validating an AMI."""

    def __init__(self, message: str, ami_id: str, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message, details={"ami_id": ami_id, **(details or {})})
        self.ami_id = ami_id


class SubnetValidationError(AWSValidationError):
    """Raised when there are issues validating a subnet."""

    def __init__(
        self, message: str, subnet_id: str, details: Optional[Dict[str, Any]] = None
    ) -> None:
        super().__init__(message, details={"subnet_id": subnet_id, **(details or {})})
        self.subnet_id = subnet_id


class SecurityGroupValidationError(AWSValidationError):
    """Raised when there are issues validating a security group."""

    def __init__(
        self,
        message: str,
        security_group_id: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message, details={"security_group_id": security_group_id, **(details or {})}
        )
        self.security_group_id = security_group_id


class ServiceQuotaError(QuotaExceededError):
    """Raised when specific AWS service quotas would be exceeded."""

    def __init__(
        self,
        message: str,
        service: str,
        quota_name: str,
        current_value: int,
        quota_value: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            details={
                "service": service,
                "quota_name": quota_name,
                "current_value": current_value,
                "quota_value": quota_value,
                **(details or {}),
            },
        )
        self.service = service
        self.quota_name = quota_name
        self.current_value = current_value
        self.quota_value = quota_value


class CostExceededError(AWSError):
    """Raised when AWS cost thresholds would be exceeded."""

    def __init__(
        self,
        message: str,
        threshold: float,
        current_cost: float,
        projected_cost: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            details={
                "threshold": threshold,
                "current_cost": current_cost,
                "projected_cost": projected_cost,
                **(details or {}),
            },
        )
        self.threshold = threshold
        self.current_cost = current_cost
        self.projected_cost = projected_cost


class IAMError(AWSError):
    """Raised when there are issues with IAM permissions."""

    def __init__(
        self,
        message: str,
        role_arn: Optional[str] = None,
        permission: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message,
            details={"role_arn": role_arn, "permission": permission, **(details or {})},
        )
        self.role_arn = role_arn
        self.permission = permission
