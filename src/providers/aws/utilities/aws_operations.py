"""
Consolidated AWS Operations Utility

This module provides integrated AWS operation patterns to eliminate duplication across handlers.
Consolidates: instance management, operation execution, describe operations, logging, and status checking.
"""

from typing import Any, Callable, Dict, List, Optional

from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from infrastructure.resilience import CircuitBreakerOpenError
from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.aws_client import AWSClient


@injectable
class AWSOperations:
    """Integrated AWS operations utility with all common patterns."""

    def __init__(self, aws_client: AWSClient, logger: LoggingPort):
        """
        Initialize AWS operations utility.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
        """
        self.aws_client = aws_client
        self._logger = logger
        self._retry_with_backoff = None  # Will be set by the handler

    def set_retry_method(self, retry_method: Callable):
        """
        Set the handler's retry method.

        Args:
            retry_method: Handler's retry method (_retry_with_backoff)
        """
        self._retry_with_backoff = retry_method

    def terminate_instances_with_fallback(
        self,
        instance_ids: List[str],
        request_adapter: Optional[Any] = None,
        operation_context: str = "instances",
    ) -> Dict[str, Any]:
        """
        Integrated instance termination with adapter fallback.

        Eliminates 60+ lines of duplication across 4 handlers.

        Args:
            instance_ids: List of instance IDs to terminate
            request_adapter: Optional request adapter for termination
            operation_context: Context for logging (e.g., "fleet instances", "ASG instances")

        Returns:
            Termination result
        """
        if not instance_ids:
            self._logger.warning("No instance IDs provided for %s termination", operation_context)
            return {"terminated_instances": []}

        self._logger.info("Terminating %s %s: %s", len(instance_ids), operation_context, instance_ids)

        try:
            if request_adapter:
                self._logger.info("Using request adapter for %s termination", operation_context)
                result = request_adapter.terminate_instances(instance_ids)
                self._logger.info("Request adapter termination result: %s", result)
                return result
            else:
                self._logger.info("Using EC2 client directly for %s termination", operation_context)
                if not self._retry_with_backoff:
                    raise ValueError("Retry method not set. Call set_retry_method first.")

                result = self._retry_with_backoff(
                    self.aws_client.ec2_client.terminate_instances,
                    operation_type="critical",
                    InstanceIds=instance_ids,
                )
                self._logger.info("Successfully terminated %s: %s", operation_context, instance_ids)
                return result

        except Exception as e:
            self._logger.error("Failed to terminate %s: %s", operation_context, str(e))
            raise

    def execute_operation_with_standard_handling(
        self,
        operation: Callable,
        operation_name: str,
        operation_type: str = "standard",
        success_message: Optional[str] = None,
        error_message: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """
        Execute AWS operation with integrated retry, logging, and exception handling.

        Eliminates 72+ lines of exception handling duplication.

        Args:
            operation: AWS operation to execute
            operation_name: Human-readable operation name for logging
            operation_type: Retry strategy type (critical/read_only/standard)
            success_message: Custom success message template
            error_message: Custom error message template
            **kwargs: Operation parameters

        Returns:
            Operation result

        Raises:
            CircuitBreakerOpenError: When circuit breaker is open
            AWSInfrastructureError: For AWS operation failures
        """
        try:
            self._logger.debug("Executing %s with operation_type=%s", operation_name, operation_type)

            if not self._retry_with_backoff:
                raise ValueError("Retry method not set. Call set_retry_method first.")

            result = self._retry_with_backoff(operation, operation_type=operation_type, **kwargs)

            if success_message:
                self._logger.info(success_message)
            else:
                self._logger.info("Successfully completed %s", operation_name)

            return result

        except CircuitBreakerOpenError as e:
            error_msg = f"Circuit breaker OPEN for {operation_name}: {str(e)}"
            self._logger.error(error_msg)
            raise e

        except ClientError as e:
            # Let the handler's _convert_client_error handle this
            raise e

        except Exception as e:
            error_msg = error_message or f"Unexpected error in {operation_name}: {str(e)}"
            self._logger.error(error_msg)
            raise AWSInfrastructureError(error_msg)

    def describe_with_pagination_and_retry(
        self, client_method: Callable, result_key: str, operation_name: str, **filters
    ) -> List[Dict[str, Any]]:
        """
        Integrated describe operations with pagination and retry.

        Eliminates 8 similar pagination patterns.

        Args:
            client_method: AWS client method to call
            result_key: Key in response containing the results
            operation_name: Operation name for logging
            **filters: AWS API filters/parameters

        Returns:
            List of resources
        """
        self._logger.debug("Describing %s with filters: %s", operation_name, filters)

        try:
            if not self._retry_with_backoff:
                raise ValueError("Retry method not set. Call set_retry_method first.")

            # Use the handler's existing _paginate method through retry
            result = self._retry_with_backoff(
                lambda: self._paginate_method(client_method, result_key, **filters),
                operation_type="read_only",
            )

            self._logger.debug("Found %s %s", len(result), operation_name)
            return result

        except Exception as e:
            self._logger.error("Failed to describe %s: %s", operation_name, str(e))
            raise

    def _paginate_method(
        self, client_method: Callable, result_key: str, **kwargs
    ) -> List[Dict[str, Any]]:
        """Access handler's pagination functionality."""
        # This will be set by the handler when initializing AWSOperations
        if hasattr(self, "_paginate_func"):
            return self._paginate_func(client_method, result_key, **kwargs)
        else:
            # Fallback to simple call without pagination
            response = client_method(**kwargs)
            return response.get(result_key, [])

    def set_pagination_method(self, paginate_func: Callable):
        """Set the handler's pagination method."""
        self._paginate_func = paginate_func

    def log_operation_start(
        self,
        operation: str,
        resource_type: str,
        resource_id: Optional[str] = None,
        **context,
    ):
        """Standardized operation start logging."""
        if resource_id:
            self._logger.info("Starting %s for %s: %s", operation, resource_type, resource_id)
        else:
            self._logger.info("Starting %s for %s", operation, resource_type)

        if context:
            self._logger.debug("%s context: %s", operation, context)

    def log_operation_success(
        self, operation: str, resource_type: str, resource_id: str, **context
    ):
        """Standardized operation success logging."""
        self._logger.info("Successfully completed %s for %s: %s", operation, resource_type, resource_id)

        if context:
            self._logger.debug("%s success context: %s", operation, context)

    def log_operation_failure(
        self,
        operation: str,
        resource_type: str,
        error: Exception,
        resource_id: Optional[str] = None,
    ):
        """Standardized operation failure logging."""
        if resource_id:
            self._logger.error(
                "Failed %s for %s %s: %s", operation, resource_type, resource_id, str(error)
            )
        else:
            self._logger.error("Failed %s for %s: %s", operation, resource_type, str(error))

    def check_resource_status(
        self,
        resource_type: str,
        resource_id: str,
        describe_method: Callable,
        status_path: str,
        expected_status: Optional[str] = None,
        **describe_params,
    ) -> str:
        """
        Integrated resource status checking.

        Args:
            resource_type: Type of resource (e.g., "EC2 Fleet", "ASG")
            resource_id: Resource identifier
            describe_method: Method to describe the resource
            status_path: Path to status in response (e.g., "FleetState", "LifecycleState")
            expected_status: Optional expected status for validation
            **describe_params: Parameters for describe method

        Returns:
            Current resource status
        """
        try:
            self._logger.debug("Checking status for %s: %s", resource_type, resource_id)

            if not self._retry_with_backoff:
                raise ValueError("Retry method not set. Call set_retry_method first.")

            response = self._retry_with_backoff(
                describe_method, operation_type="read_only", **describe_params
            )

            # Navigate to status using dot notation path
            current_status = response
            for path_part in status_path.split("."):
                if isinstance(current_status, list) and current_status:
                    current_status = current_status[0]  # Take first item for lists
                current_status = current_status.get(path_part, "unknown")

            self._logger.debug("%s %s status: %s", resource_type, resource_id, current_status)

            if expected_status and current_status != expected_status:
                self._logger.warning(
                    "%s %s status is %s, ", resource_type, resource_id, current_status
                    f"expected {expected_status}"
                )

            return str(current_status)

        except Exception as e:
            self._logger.error("Failed to check %s %s status: %s", resource_type, resource_id, str(e))
            return "unknown"

    def get_resource_instances(
        self,
        resource_type: str,
        resource_id: str,
        describe_instances_method: Callable,
        instances_key: str,
        **describe_params,
    ) -> List[str]:
        """
        Get instance IDs associated with a resource.

        Args:
            resource_type: Type of resource
            resource_id: Resource identifier
            describe_instances_method: Method to get instances
            instances_key: Key containing instances in response
            **describe_params: Parameters for describe method

        Returns:
            List of instance IDs
        """
        try:
            self._logger.debug("Getting instances for %s: %s", resource_type, resource_id)

            if not self._retry_with_backoff:
                raise ValueError("Retry method not set. Call set_retry_method first.")

            response = self._retry_with_backoff(
                describe_instances_method, operation_type="read_only", **describe_params
            )

            instances = response.get(instances_key, [])
            instance_ids = []

            for instance in instances:
                if isinstance(instance, dict):
                    instance_id = instance.get("InstanceId")
                    if instance_id:
                        instance_ids.append(instance_id)
                elif isinstance(instance, str):
                    instance_ids.append(instance)

            self._logger.debug(
                "Found %s instances for %s %s", len(instance_ids), resource_type, resource_id
            )
            return instance_ids

        except Exception as e:
            self._logger.error(
                "Failed to get instances for %s %s: %s", resource_type, resource_id, str(e)
            )
            return []

    def execute_with_standard_error_handling(
        self,
        operation: Callable,
        operation_name: str,
        context: str = "AWS operation",
        **kwargs,
    ) -> Any:
        """
        Execute AWS operation with standardized error handling.

        Consolidates the try/catch pattern used in all handlers to eliminate duplication.
        Provides consistent error conversion, logging, and exception raising.

        Args:
            operation: The AWS operation to execute
            operation_name: Human-readable name for logging
            context: Context for error messages
            **kwargs: Arguments to pass to the operation

        Returns:
            Result of the operation

        Raises:
            Appropriate domain exception based on AWS error type
        """
        try:
            self.log_operation_start(operation_name, context)
            result = operation(**kwargs)
            self.log_operation_success(operation_name, context, result)
            return result
        except ClientError as e:
            error = self._convert_client_error(e, operation_name)
            self.log_operation_failure(operation_name, context, error)
            raise error
        except Exception as e:
            error_msg = f"Failed to {operation_name}: {str(e)}"
            self._logger.error("Unexpected error in %s: %s", context, error_msg)
            raise AWSInfrastructureError(error_msg)

    def _convert_client_error(
        self, error: ClientError, operation_name: str = "AWS operation"
    ) -> Exception:
        """
        Convert AWS ClientError to appropriate domain exception.

        Consolidates error conversion logic that was duplicated across all handlers.

        Args:
            error: The AWS ClientError to convert
            operation_name: Name of the operation for error context

        Returns:
            Appropriate domain exception
        """
        error_code = error.response.get("Error", {}).get("Code", "Unknown")
        error_message = error.response.get("Error", {}).get("Message", str(error))

        # Import here to avoid circular imports
        from providers.aws.exceptions.aws_exceptions import (
            AWSEntityNotFoundError,
            AWSInfrastructureError,
            AWSPermissionError,
            AWSRateLimitError,
            AWSValidationError,
        )

        # Map AWS error codes to domain exceptions
        if error_code in [
            "InvalidParameterValue",
            "InvalidParameter",
            "ValidationException",
        ]:
            return AWSValidationError(f"{operation_name} failed: {error_message}")
        elif error_code in [
            "ResourceNotFound",
            "InvalidGroupId.NotFound",
            "InvalidInstanceID.NotFound",
        ]:
            return AWSEntityNotFoundError(f"{operation_name} failed: {error_message}")
        elif error_code in [
            "Throttling",
            "RequestLimitExceeded",
            "TooManyRequestsException",
        ]:
            return AWSRateLimitError(f"{operation_name} failed: {error_message}")
        elif error_code in ["UnauthorizedOperation", "AccessDenied", "Forbidden"]:
            return AWSPermissionError(f"{operation_name} failed: {error_message}")
        else:
            return AWSInfrastructureError(f"{operation_name} failed: {error_message}")
