"""Azure-specific exceptions.

Provides a full exception hierarchy for Azure provider operations
"""

from typing import Any, Optional

from orb.domain.base.exceptions import InfrastructureError


class AzureError(InfrastructureError):
    """Base class for Azure-related errors."""

    def __init__(
        self,
        message: str,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ) -> None:
        super().__init__(message, error_code or self.__class__.__name__, details)
        self.error_code = error_code or self.__class__.__name__

    def to_dict(self) -> dict[str, Any]:
        """Serialize the exception to a dict, including error_code if non-default."""
        result: dict[str, Any] = super().to_dict()
        if self.error_code and self.error_code != self.__class__.__name__:
            result["error_code"] = self.error_code
        return result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class AzureValidationError(AzureError):
    """Raised when Azure resource validation fails."""


class VMSizeValidationError(AzureValidationError):
    """Invalid or unsupported VM size."""

    def __init__(self, message: str, vm_size: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details={"vm_size": vm_size, **(details or {})})
        self.vm_size = vm_size


class SubnetValidationError(AzureValidationError):
    """Invalid subnet configuration."""

    def __init__(self, message: str, subnet_id: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details={"subnet_id": subnet_id, **(details or {})})
        self.subnet_id = subnet_id


class ImageValidationError(AzureValidationError):
    """Invalid image reference."""

    def __init__(self, message: str, image_ref: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details={"image_ref": image_ref, **(details or {})})
        self.image_ref = image_ref


# ---------------------------------------------------------------------------
# Entity not found
# ---------------------------------------------------------------------------

class AzureEntityNotFoundError(AzureError):
    """Raised when an Azure resource is not found."""


class VMNotFoundError(AzureEntityNotFoundError):
    """A specific VM instance was not found."""

    def __init__(self, message: str, instance_id: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details={"instance_id": instance_id, **(details or {})})
        self.instance_id = instance_id


class VMSSNotFoundError(AzureEntityNotFoundError):
    """A VMSS resource was not found."""

    def __init__(self, message: str, vmss_name: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message, details={"vmss_name": vmss_name, **(details or {})})
        self.vmss_name = vmss_name


# ---------------------------------------------------------------------------
# Quotas & capacity
# ---------------------------------------------------------------------------

class QuotaExceededError(AzureError):
    """Raised when Azure service quotas would be exceeded."""


class ServiceQuotaError(QuotaExceededError):
    """Detailed quota error with specific service information."""

    def __init__(
        self,
        message: str,
        service: str,
        quota_name: str,
        current_value: int,
        quota_value: int,
        details: Optional[dict[str, Any]] = None,
    ):
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


# ---------------------------------------------------------------------------
# Resource state
# ---------------------------------------------------------------------------

class ResourceInUseError(AzureError):
    """Raised when an Azure resource is already in use."""


class ResourceStateError(AzureError):
    """Raised when a resource is in an invalid state for the operation."""

    def __init__(
        self,
        message: str,
        resource_id: str,
        current_state: str,
        expected_states: list[str],
        details: Optional[dict[str, Any]] = None,
    ) -> None:
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


# ---------------------------------------------------------------------------
# Authentication & authorization
# ---------------------------------------------------------------------------

class AuthenticationError(AzureError):
    """Raised when Azure authentication fails."""


class AuthorizationError(AzureError):
    """Raised when there are insufficient permissions."""


class RBACError(AuthorizationError):
    """Insufficient RBAC permissions for the operation."""

    def __init__(
        self,
        message: str,
        role: Optional[str] = None,
        scope: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            details={"role": role, "scope": scope, **(details or {})},
        )
        self.role = role
        self.scope = scope


# ---------------------------------------------------------------------------
# Network & rate limiting
# ---------------------------------------------------------------------------

class RateLimitError(AzureError):
    """Raised when Azure API rate limits are exceeded."""


class NetworkError(AzureError):
    """Raised when there are network-related issues."""


# ---------------------------------------------------------------------------
# Infrastructure & configuration
# ---------------------------------------------------------------------------

class AzureInfrastructureError(AzureError):
    """Raised for general Azure infrastructure errors."""


class AzureConfigurationError(AzureError):
    """Raised when Azure configuration is invalid."""


# ---------------------------------------------------------------------------
# Provisioning
# ---------------------------------------------------------------------------

class LaunchError(AzureError):
    """Instance / VMSS launch failed."""

    def __init__(
        self,
        message: str,
        template_id: str,
        launch_params: Optional[dict[str, Any]] = None,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(
            message,
            details={
                "template_id": template_id,
                "launch_params": launch_params or {},
                **(details or {}),
            },
            error_code=error_code,
        )
        self.template_id = template_id
        self.launch_params = launch_params or {}


class VMSSCreationError(LaunchError):
    """VMSS creation specifically failed."""

    def __init__(
        self,
        message: str,
        template_id: str,
        vmss_name: Optional[str] = None,
        launch_params: Optional[dict[str, Any]] = None,
        details: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ):
        super().__init__(
            message,
            template_id=template_id,
            launch_params=launch_params,
            details={"vmss_name": vmss_name, **(details or {})},
            error_code=error_code,
        )
        self.vmss_name = vmss_name


class TerminationError(AzureError):
    """Instance termination failed."""

    def __init__(
        self,
        message: str,
        resource_ids: list[str],
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            details={"resource_ids": resource_ids, **(details or {})},
        )
        self.resource_ids = resource_ids


class ResourceCleanupError(AzureError):
    """Failed to clean up Azure resources."""

    def __init__(
        self,
        message: str,
        resource_id: str,
        resource_type: str,
        details: Optional[dict[str, Any]] = None,
    ):
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


class TaggingError(AzureError):
    """Failed to tag Azure resources."""

    def __init__(
        self,
        message: str,
        resource_id: str,
        tags: dict[str, str],
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            details={"resource_id": resource_id, "tags": tags, **(details or {})},
        )
        self.resource_id = resource_id
        self.tags = tags


# ---------------------------------------------------------------------------
# CycleCloud
# ---------------------------------------------------------------------------

class CycleCloudError(AzureError):
    """Base class for CycleCloud-related errors."""


class CycleCloudConnectionError(CycleCloudError):
    """Failed to connect to the CycleCloud REST API."""

    def __init__(
        self,
        message: str,
        url: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            details={"url": url, **(details or {})},
        )
        self.url = url


class CycleCloudClusterNotFoundError(CycleCloudError):
    """The specified CycleCloud cluster was not found."""

    def __init__(
        self,
        message: str,
        cluster_name: str,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            details={"cluster_name": cluster_name, **(details or {})},
        )
        self.cluster_name = cluster_name


class CycleCloudNodeError(CycleCloudError):
    """Failed to add or manage CycleCloud nodes."""

    def __init__(
        self,
        message: str,
        cluster_name: str,
        node_array: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ):
        super().__init__(
            message,
            details={
                "cluster_name": cluster_name,
                "node_array": node_array,
                **(details or {}),
            },
        )
        self.cluster_name = cluster_name
        self.node_array = node_array
