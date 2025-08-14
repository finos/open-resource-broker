"""
AWS Provisioning Adapter

This module provides an adapter for AWS-specific resource provisioning operations.
It implements the ResourceProvisioningPort interface from the domain layer.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.domain.base.dependency_injection import injectable
from src.domain.base.exceptions import EntityNotFoundError
from src.domain.base.ports import LoggingPort
from src.domain.request.aggregate import Request
from src.domain.template.aggregate import Template
from src.infrastructure.ports.resource_provisioning_port import ResourceProvisioningPort
from src.infrastructure.template.configuration_manager import (
    TemplateConfigurationManager,
)
from src.providers.aws.exceptions.aws_exceptions import (
    AWSEntityNotFoundError,
    AWSValidationError,
    InfrastructureError,
    QuotaExceededError,
)
from src.providers.aws.infrastructure.aws_client import AWSClient
from src.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory
from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler

if TYPE_CHECKING:
    from src.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy


@injectable
class AWSProvisioningAdapter(ResourceProvisioningPort):
    """
    AWS implementation of the ResourceProvisioningPort interface.

    This adapter uses AWS-specific handlers to provision and manage resources.
    """

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        aws_handler_factory: AWSHandlerFactory,
        template_config_manager: Optional[TemplateConfigurationManager] = None,
        provider_strategy: Optional["AWSProviderStrategy"] = None,
    ):
        """
        Initialize the adapter.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            aws_handler_factory: AWS handler factory instance
            template_config_manager: Optional template configuration manager instance
            provider_strategy: Optional AWS provider strategy for dry-run support
        """
        self._aws_client = aws_client
        self._logger = logger
        self._aws_handler_factory = aws_handler_factory
        self._template_config_manager = template_config_manager
        self._provider_strategy = provider_strategy
        self._handlers = {}  # Cache for handlers

    @property
    def aws_client(self):
        """Get the AWS client instance."""
        return self._aws_client

    async def provision_resources(self, request: Request, template: Template) -> str:
        """
        Provision AWS resources based on the request and template.

        Args:
            request: The request containing provisioning details
            template: The template to use for provisioning

        Returns:
            str: Resource identifier (e.g., fleet ID, ASG name)

        Raises:
            ValidationError: If the template is invalid
            QuotaExceededError: If resource quotas would be exceeded
            InfrastructureError: For other infrastructure errors
        """
        self._logger.info(
            f"Provisioning resources for request {request.request_id} using template {template.template_id}"
        )

        # Check if dry-run mode is requested
        is_dry_run = request.metadata.get("dry_run", False)

        if is_dry_run and self._provider_strategy:
            # Use provider strategy for dry-run operations
            return await self._provision_via_strategy(request, template, dry_run=True)
        else:
            # Use legacy handler approach for normal operations
            return self._provision_via_handlers(request, template)

    async def _provision_via_strategy(
        self, request: Request, template: Template, dry_run: bool = False
    ) -> str:
        """
        Provision resources using the provider strategy pattern.

        Args:
            request: The request to fulfill
            template: The template to use for provisioning
            dry_run: Whether to run in dry-run mode

        Returns:
            str: The resource ID
        """
        from src.providers.base.strategy import ProviderOperation, ProviderOperationType

        # Create provider operation with dry-run context
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": template.model_dump(),
                "count": request.requested_count,
                "request_id": str(request.request_id),
            },
            context={"dry_run": dry_run} if dry_run else None,
        )

        # Execute operation via provider strategy
        result = await self._provider_strategy.execute_operation(operation)

        if result.success:
            # Extract resource ID from result
            resource_id = result.data.get("instance_ids", ["dry-run-resource-id"])[0]
            self._logger.info(
                f"Successfully provisioned resources via strategy with ID {resource_id}"
            )
            return resource_id
        else:
            self._logger.error(f"Provider strategy operation failed: {result.error_message}")
            raise InfrastructureError(f"Failed to provision resources: {result.error_message}")

    def _provision_via_handlers(self, request: Request, template: Template) -> str:
        """
        Provision resources using the legacy handler approach.

        Args:
            request: The request to fulfill
            template: The template to use for provisioning

        Returns:
            str: The resource ID
        """
        # Get the appropriate handler for the template
        handler = self._get_handler_for_template(template)

        try:
            # Acquire hosts using the handler
            resource_id = handler.acquire_hosts(request, template)
            self._logger.info(f"Successfully provisioned resources with ID {resource_id}")
            return resource_id
        except AWSValidationError as e:
            self._logger.error(f"Validation error during resource provisioning: {str(e)}")
            raise
        except QuotaExceededError as e:
            self._logger.error(f"Quota exceeded during resource provisioning: {str(e)}")
            raise
        except Exception as e:
            self._logger.error(f"Error during resource provisioning: {str(e)}")
            raise InfrastructureError(f"Failed to provision resources: {str(e)}")

    def check_resources_status(self, request: Request) -> List[Dict[str, Any]]:
        """
        Check the status of provisioned AWS resources.

        Args:
            request: The request containing resource identifier

        Returns:
            List of resource details

        Raises:
            AWSEntityNotFoundError: If the resource is not found
            InfrastructureError: For other infrastructure errors
        """
        self._logger.info(f"Checking status of resources for request {request.request_id}")

        if not request.resource_id:
            self._logger.error(f"No resource ID found in request {request.request_id}")
            raise AWSEntityNotFoundError(f"No resource ID found in request {request.request_id}")

        # Get the template to determine the handler type
        if not self._template_config_manager:
            self._logger.warning(
                "TemplateConfigurationManager not injected, getting from container"
            )
            from src.infrastructure.di.container import get_container

            container = get_container()
            self._template_config_manager = container.get(TemplateConfigurationManager)

        # Ensure template_id is not None
        if not request.template_id:
            raise AWSValidationError("Template ID is required")

        # Get template using the configuration manager
        template = self._template_config_manager.get_template(str(request.template_id))
        if not template:
            raise EntityNotFoundError("Template", str(request.template_id))

        # Get the appropriate handler for the template
        handler = self._get_handler_for_template(template)

        try:
            # Check hosts status using the handler
            status = handler.check_hosts_status(request)
            self._logger.info(
                f"Successfully checked status of resources for request {request.request_id}"
            )
            return status
        except AWSEntityNotFoundError as e:
            self._logger.error(f"Resource not found during status check: {str(e)}")
            raise
        except Exception as e:
            self._logger.error(f"Error during resource status check: {str(e)}")
            raise InfrastructureError(f"Failed to check resource status: {str(e)}")

    def release_resources(self, request: Request) -> None:
        """
        Release provisioned AWS resources.

        Args:
            request: The request containing resource identifier

        Raises:
            AWSEntityNotFoundError: If the resource is not found
            InfrastructureError: For other infrastructure errors
        """
        self._logger.info(f"Releasing resources for request {request.request_id}")

        if not request.resource_id:
            self._logger.error(f"No resource ID found in request {request.request_id}")
            raise AWSEntityNotFoundError(f"No resource ID found in request {request.request_id}")

        # Get the template to determine the handler type
        if not self._template_config_manager:
            self._logger.warning(
                "TemplateConfigurationManager not injected, getting from container"
            )
            from src.infrastructure.di.container import get_container

            container = get_container()
            self._template_config_manager = container.get(TemplateConfigurationManager)

        # Ensure template_id is not None
        if not request.template_id:
            raise AWSValidationError("Template ID is required")

        # Get template using the configuration manager
        template = self._template_config_manager.get_template(str(request.template_id))
        if not template:
            raise EntityNotFoundError("Template", str(request.template_id))

        # Get the appropriate handler for the template
        handler = self._get_handler_for_template(template)

        try:
            # Release hosts using the handler
            handler.release_hosts(request)
            self._logger.info(f"Successfully released resources for request {request.request_id}")
        except AWSEntityNotFoundError as e:
            self._logger.error(f"Resource not found during release: {str(e)}")
            raise
        except Exception as e:
            self._logger.error(f"Error during resource release: {str(e)}")
            raise InfrastructureError(f"Failed to release resources: {str(e)}")

    def get_resource_health(self, resource_id: str) -> Dict[str, Any]:
        """
        Get health information for a specific AWS resource.

        Args:
            resource_id: Resource identifier

        Returns:
            Dictionary containing health information

        Raises:
            AWSEntityNotFoundError: If the resource is not found
            InfrastructureError: For other infrastructure errors
        """
        self._logger.info(f"Getting health information for resource {resource_id}")

        try:
            # Determine the resource type from the ID format
            if resource_id.startswith("i-"):
                # EC2 instance
                response = self.aws_client.ec2_client.describe_instance_status(
                    InstanceIds=[resource_id]
                )
                if not response["InstanceStatuses"]:
                    raise AWSEntityNotFoundError(f"Instance {resource_id} not found")

                status = response["InstanceStatuses"][0]
                return {
                    "resource_id": resource_id,
                    "resource_type": "ec2_instance",
                    "state": status["InstanceState"]["Name"],
                    "status": status["InstanceStatus"]["Status"],
                    "system_status": status["SystemStatus"]["Status"],
                    "details": status,
                }
            elif resource_id.startswith("fleet-"):
                # EC2 Fleet
                response = self.aws_client.ec2_client.describe_fleets(FleetIds=[resource_id])
                if not response["Fleets"]:
                    raise AWSEntityNotFoundError(f"Fleet {resource_id} not found")

                fleet = response["Fleets"][0]
                return {
                    "resource_id": resource_id,
                    "resource_type": "ec2_fleet",
                    "state": fleet["FleetState"],
                    "status": ("active" if fleet["FleetState"] == "active" else "inactive"),
                    "target_capacity": fleet["TargetCapacitySpecification"]["TotalTargetCapacity"],
                    "fulfilled_capacity": fleet.get("FulfilledCapacity", 0),
                    "details": fleet,
                }
            elif resource_id.startswith("sfr-"):
                # Spot Fleet
                response = self.aws_client.ec2_client.describe_spot_fleet_requests(
                    SpotFleetRequestIds=[resource_id]
                )
                if not response["SpotFleetRequestConfigs"]:
                    raise AWSEntityNotFoundError(f"Spot Fleet {resource_id} not found")

                fleet = response["SpotFleetRequestConfigs"][0]
                return {
                    "resource_id": resource_id,
                    "resource_type": "spot_fleet",
                    "state": fleet["SpotFleetRequestState"],
                    "status": (
                        "active" if fleet["SpotFleetRequestState"] == "active" else "inactive"
                    ),
                    "target_capacity": fleet["SpotFleetRequestConfig"]["TargetCapacity"],
                    "fulfilled_capacity": fleet.get("FulfilledCapacity", 0),
                    "details": fleet,
                }
            else:
                # Try to determine the resource type from the AWS API
                # This is a simplified approach and might need to be expanded
                try:
                    # Try as ASG
                    response = self.aws_client.autoscaling_client.describe_auto_scaling_groups(
                        AutoScalingGroupNames=[resource_id]
                    )
                    if response["AutoScalingGroups"]:
                        asg = response["AutoScalingGroups"][0]
                        return {
                            "resource_id": resource_id,
                            "resource_type": "auto_scaling_group",
                            "status": "active",
                            "desired_capacity": asg["DesiredCapacity"],
                            "current_capacity": len(asg["Instances"]),
                            "details": asg,
                        }
                except Exception as e:
                    self._logger.warning(
                        f"Failed to process auto scaling group details: {e}",
                        extra={"resource_id": resource_id},
                    )

                # If we get here, we couldn't determine the resource type
                raise AWSEntityNotFoundError(
                    f"Resource {resource_id} not found or type not supported"
                )
        except AWSEntityNotFoundError:
            raise
        except Exception as e:
            self._logger.error(f"Error getting resource health: {str(e)}")
            raise InfrastructureError(f"Failed to get resource health: {str(e)}")

    def _get_handler_for_template(self, template: Template) -> AWSHandler:
        """
        Get the appropriate AWS handler for the template.

        Args:
            template: The template to get a handler for

        Returns:
            AWSHandler: The appropriate handler for the template

        Raises:
            ValidationError: If the template has an invalid handler type
        """
        # Check if we already have a cached handler for this type
        handler_type = template.provider_api
        if handler_type in self._handlers:
            return self._handlers[handler_type]

        # Use the handler factory to create the handler
        handler = self._aws_handler_factory.create_handler(handler_type)

        # Cache the handler for future use
        self._handlers[handler_type] = handler
        return handler
