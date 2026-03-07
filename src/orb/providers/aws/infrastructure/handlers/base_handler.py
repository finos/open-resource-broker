"""
Integrated AWS Handler Base Class following Clean Architecture and CQRS patterns.

This module provides a integrated base handler that combines the best features of both
AWSHandler and BaseAWSHandler patterns while maintaining clean architecture principles
and clean integration with our DI/CQRS system.
"""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Callable, Optional, TypeVar

from botocore.exceptions import ClientError

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import ErrorHandlingPort, LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.resilience import retry
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import (
    AuthorizationError,
    AWSEntityNotFoundError,
    AWSValidationError,
    InfrastructureError,
    NetworkError,
    QuotaExceededError,
    RateLimitError,
    ResourceInUseError,
)
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.tags import build_resource_tags

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
        machine_adapter=None,
        error_handler: Optional[ErrorHandlingPort] = None,
        aws_native_spec_service: Optional[Any] = None,
        config_port: Optional[ConfigurationPort] = None,
    ) -> None:
        """
        Initialize AWS handler with standardized dependencies.

        Args:
            aws_client: AWS client for API operations
            logger: Logging port for operation logging
            aws_ops: AWS operations utility (required)
            launch_template_manager: Launch template manager (required)
            request_adapter: Request adapter for terminating instances (optional)
            machine_adapter: Machine adapter for provider-specific instance mapping (optional)
            error_handler: Error handling port for exception management (optional)
        """
        self.aws_client = aws_client
        self._logger = logger
        self.launch_template_manager = launch_template_manager
        self._machine_adapter = machine_adapter
        self.error_handler = error_handler
        self.aws_native_spec_service = aws_native_spec_service
        self.config_port = config_port
        self.max_retries = 3
        self.base_delay = 1  # seconds
        self.max_delay = 10  # seconds

        # Setup required dependencies
        self._setup_aws_operations(aws_ops)
        self._setup_dependencies(request_adapter, machine_adapter)

    def _setup_aws_operations(self, aws_ops) -> None:
        """Configure AWS operations utility - eliminates duplication across handlers."""
        self.aws_ops = aws_ops
        if hasattr(aws_ops, "set_retry_method"):
            aws_ops.set_retry_method(self._retry_with_backoff)
        if hasattr(aws_ops, "set_pagination_method"):
            aws_ops.set_pagination_method(self._paginate)

    def _setup_dependencies(self, request_adapter, machine_adapter) -> None:
        """Configure optional dependencies - eliminates duplication across handlers."""
        self._request_adapter = request_adapter

        # Standardized logging for request adapter status
        if request_adapter:
            self._logger.debug("Successfully initialized request adapter")
        else:
            self._logger.debug("No request adapter provided, will use EC2 client directly")

        if machine_adapter:
            self._logger.debug("Machine adapter provided for AWS handler")

    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> str:
        """
        Acquire hosts using the specified AWS template.

        Validates common prerequisites then delegates to _acquire_hosts_internal.

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
        self._validate_prerequisites(aws_template)
        return self._acquire_hosts_internal(request, aws_template)

    @abstractmethod
    def _acquire_hosts_internal(self, request: Request, aws_template: AWSTemplate) -> str:
        """
        Handler-specific host acquisition logic.

        Called by acquire_hosts after common prerequisites have been validated.
        Subclasses implement their AWS-specific provisioning here.

        Args:
            request: The request to fulfill
            aws_template: The AWS template to use

        Returns:
            str: The AWS resource ID (e.g., fleet ID, ASG name)
        """

    @abstractmethod
    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
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

    def _extract_instance_ids(self, api_response: dict[str, Any], extractor: Any) -> list[str]:
        """Extract instance IDs from API response if available."""
        return extractor(api_response)

    def _format_instance_data(
        self,
        instance_details: list[dict[str, Any]],
        resource_id: str,
        provider_api_value: str,
    ) -> list[dict[str, Any]]:
        """Stamp resource_id and provider_api onto each instance dict.

        instance_details is already in snake_case domain format from _get_instance_details.
        Subclasses resolve provider_api_value via _resolve_provider_api and pass it here.
        """
        result = []
        for inst in instance_details:
            stamped = dict(inst)
            stamped.setdefault("resource_id", resource_id)
            stamped.setdefault("provider_api", provider_api_value)
            result.append(stamped)
        return result

    @abstractmethod
    def release_hosts(
        self,
        machine_ids: list[str],
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
        request_id: str = "",
    ) -> None:
        """
        Release hosts by instance ID.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: Optional mapping of instance_id to (resource_id, desired_capacity)
                              for intelligent resource management (e.g. ASG/fleet capacity reduction)
            request_id: Original provisioning request ID, used for launch template cleanup

        Raises:
            AWSEntityNotFoundError: If the AWS resource is not found
            InfrastructureError: For other AWS API errors
        """

    @abstractmethod
    def cancel_resource(self, resource_id: str, request_id: str) -> dict[str, Any]:
        """
        Cancel the AWS resource associated with a request.

        Called when a request needs to be cancelled before instances are
        assigned. Each handler implements the appropriate teardown for its
        resource type (delete fleet, cancel spot fleet, delete ASG, etc.).

        Args:
            resource_id: The AWS resource ID (fleet ID, ASG name, etc.)
            request_id: The ORB request ID, used for launch template cleanup

        Returns:
            Dictionary with cancellation results containing at minimum a
            ``status`` key of ``"success"`` or ``"error"``.
        """

    @classmethod
    @abstractmethod
    def get_example_templates(cls) -> list[Template]:
        """
        Get example templates for this handler.

        Returns:
            List of example Template objects for this handler type
        """

    def _retry_with_backoff(
        self,
        func: Callable[..., T],
        *args,
        operation_type: str = "standard",
        non_retryable_errors: Optional[list[str]] = None,
        log_payload: bool = True,
        log_response: bool = True,
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

        operation_name = getattr(func, "__name__", repr(func))

        def _format_debug_data(data: Any) -> str:
            try:
                return json.dumps(data, default=str, indent=2, sort_keys=True)
            except Exception:
                return str(data)

        # Create retry decorator with appropriate strategy
        @retry(**strategy_config)
        def wrapped_operation():
            """Wrapped operation with retry logic applied."""
            if log_payload:
                payload_snapshot = {"args": args, "kwargs": kwargs}
                self._logger.debug(
                    "Calling AWS operation %s with payload:\n%s",
                    operation_name,
                    _format_debug_data(payload_snapshot),
                )
            return func(*args, **kwargs)

        try:
            result = wrapped_operation()
            if log_response:
                self._logger.debug(
                    "AWS operation %s response:\n%s",
                    operation_name,
                    _format_debug_data(result),
                )
            return result
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
                raise

            # Convert AWS ClientError to domain exception
            if isinstance(e, ClientError):
                raise self._convert_client_error(e, operation_name)

            # Re-raise other exceptions as-is
            raise

    def _get_service_name(self) -> str:
        """Get service name from handler class name."""
        return self.__class__.__name__.replace("Handler", "").lower()

    def _get_retry_strategy_config(
        self,
        operation_type: str,
        service_name: str,
        operation_name: Optional[str] = None,
    ) -> dict[str, Any]:
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
            self._logger.debug("Auto-detected critical operation: %s", operation_name)

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
        elif error_code in [
            "LimitExceeded",
            "InstanceLimitExceeded",
            "VcpuLimitExceeded",
            "MaxSpotInstanceCountExceeded",
            "ServiceQuotaExceededException",
            "ResourceCountExceeded",
        ]:
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

    def _paginate(self, client_method: Callable, result_key: str, **kwargs) -> list[dict[str, Any]]:
        """
        Paginate through AWS API results.

        Args:
            client_method: The AWS client method to call
            result_key: The key in the response containing the results
            **kwargs: Arguments to pass to the client method

        Returns:
            Combined results from all pages
        """
        from orb.providers.aws.infrastructure.utils import paginate

        return paginate(client_method, result_key, **kwargs)

    def _collect_with_next_token(
        self,
        client_method: Callable,
        result_key: str,
        request_token_param: str = "NextToken",  # nosec B107
        response_token_key: str = "NextToken",  # nosec B107
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Collect results from an AWS operation that uses NextToken-based pagination
        but does not expose a paginator in botocore.

        Args:
            client_method: AWS client method to invoke
            result_key: Key in the response that contains results
            request_token_param: Request parameter for pagination token
            response_token_key: Response key containing the next pagination token
            **kwargs: Additional arguments for the client method

        Returns:
            Aggregated list of result items
        """
        combined_results: list[dict[str, Any]] = []
        next_token: str | None = None

        while True:
            request_kwargs = dict(kwargs)
            if next_token:
                request_kwargs[request_token_param] = next_token

            response = client_method(**request_kwargs)
            combined_results.extend(response.get(result_key, []))

            next_token = response.get(response_token_key)
            if not next_token:
                break

        return combined_results

    def _build_fallback_machine_payload(
        self, inst: dict[str, Any], resource_id: str
    ) -> dict[str, Any]:
        """Construct minimal machine payload when machine adapter is unavailable."""
        state = inst.get("State")
        status = state.get("Name") if isinstance(state, dict) else state

        launch_time = inst.get("LaunchTime")
        if isinstance(launch_time, datetime):
            launch_time = launch_time.isoformat()

        return {
            "instance_id": inst.get("InstanceId"),
            "resource_id": resource_id,
            "status": status,
            "private_ip": inst.get("PrivateIpAddress"),
            "public_ip": inst.get("PublicIpAddress"),
            "launch_time": launch_time,
            "instance_type": inst.get("InstanceType"),
            "image_id": inst.get("ImageId"),
            "subnet_id": inst.get("SubnetId"),
            "security_group_ids": [sg["GroupId"] for sg in inst.get("SecurityGroups", [])],
            "vpc_id": inst.get("VpcId"),
        }

    def _get_instance_details(
        self,
        instance_ids: list[str],
        provider_api: str,
        request_id: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        Get detailed information about EC2 instances using machine adapter for proper formatting.

        Args:
            instance_ids: List of instance IDs to describe
            request_id: Request ID for machine adapter context (optional)
            resource_id: Resource ID (fleet ID, ASG name, etc.) for machine adapter context (optional)
            provider_api: Provider API type for machine adapter (default: "EC2")

        Returns:
            List of instance details in proper snake_case domain format

        Raises:
            AWSEntityNotFoundError: If any instance is not found
            InfrastructureError: For other AWS API errors
        """
        try:
            # Use AWS client's EC2 client for describe_instances
            response = self.aws_client.ec2_client.describe_instances(InstanceIds=instance_ids)

            instances: list[dict[str, Any]] = []
            reservations = response.get("Reservations", [])
            self._logger.debug(
                "Retrieved %d reservations for %d instance IDs",
                len(reservations),
                len(instance_ids),
            )

            for reservation in reservations:
                for instance in reservation.get("Instances", []):
                    # Use machine adapter if available for proper snake_case formatting
                    if self._machine_adapter and request_id and resource_id:
                        try:
                            # Let machine adapter handle the conversion to proper snake_case format
                            machine_data = self._machine_adapter.create_machine_from_aws_instance(
                                instance,
                                request_id=request_id,
                                provider_api=provider_api,
                                resource_id=resource_id,
                            )
                            instances.append(machine_data)
                            self._logger.debug(
                                "Successfully converted instance %s using machine adapter",
                                instance.get("InstanceId"),
                            )
                        except Exception as e:
                            self._logger.warning(
                                "Machine adapter failed for instance %s, using fallback: %s",
                                instance.get("InstanceId"),
                                e,
                            )
                            # Fallback to existing method
                            instances.append(
                                self._build_fallback_machine_payload(
                                    instance, resource_id or "unknown"
                                )
                            )
                    else:
                        # Fallback when machine adapter not available or missing context
                        self._logger.debug(
                            "Using fallback conversion for instance %s (adapter=%s, request_id=%s, resource_id=%s)",
                            instance.get("InstanceId"),
                            bool(self._machine_adapter),
                            bool(request_id),
                            bool(resource_id),
                        )
                        instances.append(
                            self._build_fallback_machine_payload(instance, resource_id or "unknown")
                        )

            self._logger.debug("Converted %d instances to domain format", len(instances))
            return instances

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to get instance details: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error getting instance details: %s", str(e))
            raise InfrastructureError(f"Failed to get instance details: {e!s}")

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
        if not template.machine_types:
            errors["instanceType"] = "machine_types must be specified"

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
            raise AWSValidationError(detailed_message, errors)

    def get_provider_info(self) -> dict[str, Any]:
        """Return basic provider metadata for this handler.

        Satisfies the ProviderMonitoringPort contract. Subclasses may override
        to add handler-specific fields.
        """
        return {
            "provider_type": "aws",
            "handler_type": self.get_handler_type(),
        }

    # Utility methods for AWS operations (keeping existing functionality)
    def get_handler_type(self) -> str:
        """Get handler type from class name."""
        return self.__class__.__name__.replace("Handler", "").lower()

    def _get_default_capacity_type(self, price_type: str) -> str:
        """Get default target capacity type based on price type."""
        if price_type == "spot":
            return "spot"
        elif price_type == "ondemand":
            return "on-demand"
        else:  # heterogeneous or None
            return "on-demand"

    def _build_resource_tags(
        self,
        request_id: str,
        template: AWSTemplate,
        resource_prefix_key: str,
        provider_api: str,
    ) -> list[dict]:
        """Build the flat tag list for an AWS resource.

        Combines a Name tag (prefix + request_id), any template-level tags, and
        the standard ORB system tags into a single merged list.

        Args:
            request_id:          The ORB request UUID (string).
            template:            The AWS template (provides template_id and tags).
            resource_prefix_key: Key passed to config_port.get_resource_prefix
                                 (e.g. "fleet", "spot_fleet", "asg").
            provider_api:        AWS API label for the system tag (e.g. "EC2Fleet").

        Returns:
            Merged list of {"Key": k, "Value": v} dicts ready for AWS API calls.
        """
        assert self.config_port is not None, "config_port must be injected"
        return build_resource_tags(
            config_port=self.config_port,
            request_id=request_id,
            template_id=str(template.template_id),
            resource_prefix_key=resource_prefix_key,
            provider_api=provider_api,
            template_tags=template.tags,
        )

    def _cleanup_on_zero_capacity(self, resource_type: str, request_id: str) -> None:
        """Delete the ORB-managed launch template when a resource reaches zero capacity.

        Reads the cleanup config, checks that cleanup is enabled and that the
        resource type is included in the ``resources`` allow-list, then delegates
        to ``_delete_orb_launch_template``.  All failures are warning-only so
        that cleanup never blocks the main return flow.

        Args:
            resource_type: Cleanup config resource key, e.g. ``"asg"``,
                ``"ec2_fleet"``, or ``"spot_fleet"``.
            request_id: The ORB request ID used to locate the launch template.
        """
        if self.config_port is None:
            return

        try:
            cleanup = self.config_port.get_cleanup_config()
        except Exception:
            return

        if not cleanup.get("enabled", True):
            return

        if not cleanup.get("resources", {}).get(resource_type, True):
            return

        self._delete_orb_launch_template(request_id)

    def _delete_orb_launch_template(self, request_id: str) -> None:
        """Delete the ORB-managed launch template for a request, if one exists.

        Reconstructs the launch template name from the request ID, verifies the
        ``orb:managed-by`` tag to confirm ORB ownership, then deletes it.
        Respects the cleanup config dry_run flag.  All failures are warning-only
        so that LT cleanup never blocks the main return flow.
        """
        if self.config_port is None:
            self._logger.warning(
                "config_port not injected; skipping launch template cleanup for %s", request_id
            )
            return

        try:
            cleanup = self.config_port.get_cleanup_config()
        except Exception as e:
            self._logger.warning("Could not read cleanup config, skipping LT cleanup: %s", e)
            return

        if not cleanup.get("enabled", True) or not cleanup.get("delete_launch_template", True):
            return

        lt_name = f"{self.config_port.get_resource_prefix('launch_template')}{request_id}"
        dry_run = cleanup.get("dry_run", False)

        try:
            response = self.aws_client.ec2_client.describe_launch_templates(
                LaunchTemplateNames=[lt_name]
            )
            templates = response.get("LaunchTemplates", [])
            if not templates:
                self._logger.debug(
                    "No launch template named %s found; nothing to clean up", lt_name
                )
                return

            lt = templates[0]
            tags = {t["Key"]: t["Value"] for t in lt.get("Tags", [])}
            if tags.get("orb:managed-by") != "open-resource-broker":
                self._logger.warning(
                    "Launch template %s is not ORB-managed (orb:managed-by tag absent or wrong);"
                    " skipping deletion",
                    lt_name,
                )
                return

            lt_id = lt["LaunchTemplateId"]

            if dry_run:
                self._logger.info(
                    "[dry-run] Would delete launch template %s (%s) for request %s",
                    lt_name,
                    lt_id,
                    request_id,
                )
                return

            self.aws_client.ec2_client.delete_launch_template(LaunchTemplateId=lt_id)
            self._logger.info(
                "Deleted launch template %s (%s) for request %s", lt_name, lt_id, request_id
            )

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "InvalidLaunchTemplateName.NotFoundException":
                self._logger.debug("Launch template %s not found; nothing to clean up", lt_name)
            else:
                self._logger.warning(
                    "Failed to delete launch template %s for request %s: %s",
                    lt_name,
                    request_id,
                    e,
                )
        except Exception as e:
            self._logger.warning(
                "Unexpected error deleting launch template %s for request %s: %s",
                lt_name,
                request_id,
                e,
            )
