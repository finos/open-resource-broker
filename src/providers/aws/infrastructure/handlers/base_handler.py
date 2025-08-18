"""
Integrated AWS Handler Base Class following Clean Architecture and CQRS patterns.

This module provides a integrated base handler that combines the best features of both
AWSHandler and BaseAWSHandler patterns while maintaining clean architecture principles
and clean integration with our DI/CQRS system.
"""

from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, TypeVar

from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import ErrorHandlingPort, LoggingPort
from domain.request.aggregate import Request
from infrastructure.resilience import retry
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import (
    AuthorizationError,
    AWSEntityNotFoundError,
    AWSValidationError,
    InfrastructureError,
    NetworkError,
    QuotaExceededError,
    RateLimitError,
    ResourceInUseError,
)
from providers.aws.infrastructure.aws_client import AWSClient

T = TypeVar("T")


@injectable
class AWSHandler(ABC):
    """
    Integrated AWS handler base class following Clean Architecture and CQRS patterns.

    This class provides the foundation for all AWS handlers in the system,
    combining the best features of both synchronous and asynchronous patterns:

    - Clean Architecture compliance with dependency injection
    - CQRS-aligned error handling and logging
    - Professional retry logic with circuit breaker support
    - Performance monitoring and metrics collection
    - Consistent constructor pattern across all handlers
    - Template method pattern for extensibility
    - AWS-specific optimizations and error handling

    Architecture Alignment:
    - Follows same patterns as other base handlers in the system
    - Appropriate DI integration with standardized dependencies
    - Clean separation of concerns
    - Professional error handling and logging
    """

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        aws_ops,
        launch_template_manager,
        request_adapter=None,
        error_handler: Optional[ErrorHandlingPort] = None,
    ) -> None:
        """
        Initialize AWS handler with standardized dependencies.

        Args:
            aws_client: AWS client for API operations
            logger: Logging port for operation logging
            aws_ops: AWS operations utility (required)
            launch_template_manager: Launch template manager (required)
            request_adapter: Request adapter for terminating instances (optional)
            error_handler: Error handling port for exception management (optional)
        """
        self.aws_client = aws_client
        self._logger = logger
        self.launch_template_manager = launch_template_manager
        self.error_handler = error_handler
        self.max_retries = 3
        self.base_delay = 1  # seconds
        self.max_delay = 10  # seconds
        self._metrics: Dict[str, Any] = {}

        # Setup required dependencies
        self._setup_aws_operations(aws_ops)
        self._setup_dependencies(request_adapter)

    def _setup_aws_operations(self, aws_ops) -> None:
        """Configure AWS operations utility - eliminates duplication across handlers."""
        self.aws_ops = aws_ops
        if hasattr(aws_ops, "set_retry_method"):
            aws_ops.set_retry_method(self._retry_with_backoff)
        if hasattr(aws_ops, "set_pagination_method"):
            aws_ops.set_pagination_method(self._paginate)

    def _setup_dependencies(self, request_adapter) -> None:
        """Configure optional dependencies - eliminates duplication across handlers."""
        self._request_adapter = request_adapter

        # Standardized logging for request adapter status
        if request_adapter:
            self._logger.debug("Successfully initialized request adapter")
        else:
            self._logger.debug("No request adapter provided, will use EC2 client directly")

    @abstractmethod
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> str:
        """
        Acquire hosts using the specified AWS template.

        Args:
            request: The request to fulfill
            aws_template: The AWS template to use

        Returns:
            str: The AWS resource ID (e.g., fleet ID, ASG name)

        Raises:
            AWSValidationError: If the template is invalid
            QuotaExceededError: If AWS quotas would be exceeded
            InfrastructureError: For other AWS API errors
        """

    @abstractmethod
    def check_hosts_status(self, request: Request) -> List[Dict[str, Any]]:
        """
        Check the status of hosts for a request.

        Args:
            request: The request to check

        Returns:
            List of instance details

        Raises:
            AWSEntityNotFoundError: If the AWS resource is not found
            InfrastructureError: For other AWS API errors
        """

    @abstractmethod
    def release_hosts(self, request: Request) -> None:
        """
        Release hosts associated with a request.

        Args:
            request: The request containing hosts to release

        Raises:
            AWSEntityNotFoundError: If the AWS resource is not found
            InfrastructureError: For other AWS API errors
        """

    def _retry_with_backoff(
        self,
        func: Callable[..., T],
        *args,
        operation_type: str = "standard",
        non_retryable_errors: List[str] = None,
        **kwargs,
    ) -> T:
        """
        Execute a function with operation-specific retry and circuit breaker strategy.

        Args:
            func: Function to execute
            *args: Positional arguments for the function
            operation_type: Type of operation (critical, standard, read_only)
            non_retryable_errors: List of error codes that should not be retried (for compatibility)
            **kwargs: Keyword arguments for the function

        Returns:
            The function's return value

        Raises:
            CircuitBreakerOpenError: When circuit breaker is open
            The last error encountered after all retries
        """
        # Get operation details
        operation_name = getattr(func, "__name__", "aws_operation")
        service_name = self._get_service_name()

        # Determine retry strategy based on operation type
        strategy_config = self._get_retry_strategy_config(
            operation_type, service_name, operation_name
        )

        # Create retry decorator with appropriate strategy
        @retry(**strategy_config)
        def wrapped_operation():
            """Wrapped operation with retry logic applied."""
            return func(*args, **kwargs)

        try:
            return wrapped_operation()
        except Exception as e:
            # Handle circuit breaker exceptions
            if hasattr(e, "__class__") and "CircuitBreakerOpenError" in str(type(e)):
                # Log circuit breaker state and re-raise
                self._logger.error(
                    "Circuit breaker OPEN for %s.%s",
                    service_name,
                    operation_name,
                    extra={
                        "service": service_name,
                        "operation": operation_name,
                        "operation_type": operation_type,
                    },
                )
                raise e

            # Convert AWS ClientError to domain exception
            if isinstance(e, ClientError):
                raise self._convert_client_error(e, operation_name)

            # Re-raise other exceptions as-is
            raise e

    def _get_service_name(self) -> str:
        """Get service name from handler class name."""
        return self.__class__.__name__.replace("Handler", "").lower()

    def _get_retry_strategy_config(
        self, operation_type: str, service_name: str, operation_name: str = None
    ) -> Dict[str, Any]:
        """
        Get retry strategy configuration based on operation type.

        Args:
            operation_type: Type of operation (critical, standard, read_only)
            service_name: AWS service name
            operation_name: Specific operation name for auto-detection

        Returns:
            Dictionary with retry configuration
        """
        # Define critical operations that need circuit breaker
        critical_operations = {
            "create_fleet",
            "request_spot_fleet",
            "create_auto_scaling_group",
            "run_instances",
            "modify_fleet",
            "delete_fleets",
            "cancel_spot_fleet_requests",
            "update_auto_scaling_group",
            "delete_auto_scaling_group",
        }

        # Auto-detect critical operations if not explicitly specified
        if (
            operation_type == "standard"
            and operation_name
            and operation_name in critical_operations
        ):
            operation_type = "critical"
            self.logger.debug("Auto-detected critical operation: %s", operation_name)

        if operation_type == "critical":
            # Use circuit breaker for critical operations
            return {
                "strategy": "circuit_breaker",
                "service": service_name,
                "max_attempts": 3,
                "base_delay": 1.0,
                "max_delay": 30.0,
                "jitter": True,
                "failure_threshold": 5,
                "reset_timeout": 60,
                "half_open_timeout": 30,
            }
        elif operation_type == "read_only":
            # Use lighter retry for read operations
            return {
                "strategy": "exponential",
                "service": service_name,
                "max_attempts": 2,
                "base_delay": 0.5,
                "max_delay": 10.0,
            }
        else:
            # Standard exponential backoff for regular operations
            return {
                "strategy": "exponential",
                "service": service_name,
                "max_attempts": 3,
                "base_delay": 1.0,
                "max_delay": 30.0,
            }

    def _convert_client_error(
        self, error: ClientError, operation_name: str = "unknown"
    ) -> Exception:
        """Convert AWS ClientError to domain exception."""
        error_code = error.response["Error"]["Code"]
        error_message = error.response["Error"]["Message"]

        if error_code in ["ValidationError", "InvalidParameterValue"]:
            return AWSValidationError(error_message)
        elif error_code in ["LimitExceeded", "InstanceLimitExceeded"]:
            return QuotaExceededError(error_message)
        elif error_code == "ResourceInUse":
            return ResourceInUseError(error_message)
        elif error_code in ["UnauthorizedOperation", "AccessDenied"]:
            return AuthorizationError(error_message)
        elif error_code == "RequestLimitExceeded":
            return RateLimitError(error_message)
        elif error_code in ["ResourceNotFound", "InvalidInstanceID.NotFound"]:
            return AWSEntityNotFoundError(error_message)
        elif error_code in ["RequestTimeout", "ServiceUnavailable"]:
            return NetworkError(error_message)
        else:
            return InfrastructureError(f"AWS Error: {error_code} - {error_message}")

    def _paginate(self, client_method: Callable, result_key: str, **kwargs) -> List[Dict[str, Any]]:
        """
        Paginate through AWS API results.

        Args:
            client_method: The AWS client method to call
            result_key: The key in the response containing the results
            **kwargs: Arguments to pass to the client method

        Returns:
            Combined results from all pages
        """
        from providers.aws.infrastructure.utils import paginate

        return paginate(client_method, result_key, **kwargs)

    def _get_instance_details(self, instance_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Get detailed information about EC2 instances.

        Args:
            instance_ids: List of instance IDs to describe

        Returns:
            List of instance details

        Raises:
            AWSEntityNotFoundError: If any instance is not found
            InfrastructureError: For other AWS API errors
        """
        try:
            # Use AWS client's EC2 client for describe_instances
            response = self.aws_client.ec2_client.describe_instances(InstanceIds=instance_ids)

            instances = []
            for reservation in response.get("Reservations", []):
                for instance in reservation["Instances"]:
                    instances.append(
                        {
                            "InstanceId": instance["InstanceId"],
                            "State": instance["State"]["Name"],
                            "PrivateIpAddress": instance.get("PrivateIpAddress"),
                            "PublicIpAddress": instance.get("PublicIpAddress"),
                            "LaunchTime": instance["LaunchTime"].isoformat(),
                            "Tags": instance.get("Tags", []),
                            "InstanceType": instance["InstanceType"],
                        }
                    )

            return instances

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to get instance details: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error getting instance details: %s", str(e))
            raise InfrastructureError(f"Failed to get instance details: {str(e)}")

    def _validate_prerequisites(self, template: AWSTemplate) -> None:
        """
        Validate AWS template prerequisites.

        Args:
            template: The AWS template to validate

        Raises:
            AWSValidationError: If prerequisites are not met
        """
        errors = {}

        # Validate image ID
        if not template.image_id:
            errors["imageId"] = "Image ID is required"
        # Skip AMI ID format validation as it might have been updated by AWSTemplateAdapter
        # The actual AWS API call will validate the AMI ID format

        # Validate instance type(s)
        if not (template.instance_type or template.instance_types):
            errors["instanceType"] = "Either instance_type or instance_types must be specified"
        if template.instance_type and template.instance_types:
            errors["instanceType"] = "Cannot specify both instance_type and instance_types"

        # Validate subnet(s) - subnet_id is a property of subnet_ids, so only
        # check subnet_ids
        if not template.subnet_ids:
            errors["subnet"] = "At least one subnet must be specified in subnet_ids"

        # Validate security groups
        if not template.security_group_ids:
            errors["securityGroups"] = "At least one security group is required"

        if errors:
            # Create detailed error message
            error_details = []
            for field, message in errors.items():
                error_details.append(f"{field}: {message}")

            detailed_message = f"Template validation failed - {'; '.join(error_details)}"
            raise AWSValidationError(detailed_message, errors) from e

    # Performance monitoring methods
    def _record_success_metrics(self, request_type: str, duration: float) -> None:
        """Record success metrics for monitoring."""
        key = f"aws_{request_type}"
        if key not in self._metrics:
            self._metrics[key] = {
                "success_count": 0,
                "failure_count": 0,
                "total_duration": 0.0,
                "avg_duration": 0.0,
            }

        metrics = self._metrics[key]
        metrics["success_count"] += 1
        metrics["total_duration"] += duration
        total_count = metrics["success_count"] + metrics["failure_count"]
        metrics["avg_duration"] = (
            metrics["total_duration"] / total_count if total_count > 0 else 0.0
        )

    def _record_failure_metrics(self, request_type: str, duration: float, error: Exception) -> None:
        """Record failure metrics for monitoring."""
        key = f"aws_{request_type}"
        if key not in self._metrics:
            self._metrics[key] = {
                "success_count": 0,
                "failure_count": 0,
                "total_duration": 0.0,
                "avg_duration": 0.0,
                "last_error": None,
            }

        metrics = self._metrics[key]
        metrics["failure_count"] += 1
        metrics["total_duration"] += duration
        metrics["last_error"] = str(error)
        total_count = metrics["success_count"] + metrics["failure_count"]
        metrics["avg_duration"] = (
            metrics["total_duration"] / total_count if total_count > 0 else 0.0
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Get handler performance metrics."""
        return self._metrics.copy()

    # Utility methods for AWS operations (keeping existing functionality)
    def get_handler_type(self) -> str:
        """Get handler type from class name."""
        return self.__class__.__name__.replace("Handler", "").lower()
