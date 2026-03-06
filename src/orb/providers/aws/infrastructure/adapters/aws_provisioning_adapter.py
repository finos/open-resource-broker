"""
AWS Provisioning Adapter

This module provides an adapter for AWS-specific resource provisioning operations.
It implements the ResourceProvisioningPort interface from the domain layer.
"""

from typing import Any, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.adapters.ports.resource_provisioning_port import (
    ResourceProvisioningPort,
)
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSEntityNotFoundError,
    AWSValidationError,
    InfrastructureError,
    QuotaExceededError,
)
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler

# Removed TYPE_CHECKING import to avoid circular dependency issues during DI resolution
# if TYPE_CHECKING:
#     from providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy


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
        provider_strategy: Any,  # AWSProviderStrategy
        template_config_manager: Optional[TemplateConfigurationManager] = None,
        config_port: Optional[ConfigurationPort] = None,
    ) -> None:
        """
        Initialize the adapter.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            provider_strategy: AWS provider strategy for handler creation
            template_config_manager: Optional template configuration manager instance
            config_port: Configuration port for accessing application config
        """
        self._aws_client = aws_client
        self._logger = logger
        self._provider_strategy = provider_strategy
        self._template_config_manager = template_config_manager
        self._config_port = config_port
        self._handlers = {}  # Cache for handlers

    @property
    def aws_client(self):
        """Get the AWS client instance."""
        return self._aws_client

    async def provision_resources(self, request: Request, template: Template) -> dict[str, Any]:  # type: ignore[override]
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
            "Provisioning resources for request %s using template %s",
            request.request_id,
            template.template_id,
        )

        # Check if dry-run mode is requested
        is_dry_run = request.metadata.get("dry_run", False)

        return self._provision_via_handlers(request, template, dry_run=is_dry_run)

    async def _provision_via_strategy(
        self, request: Request, template: Template, dry_run: bool = False
    ) -> dict[str, Any]:  # type: ignore[return]
        """
        Provision resources using the provider strategy pattern.

        Args:
            request: The request to fulfill
            template: The template to use for provisioning
            dry_run: Whether to run in dry-run mode

        Returns:
            str: The resource ID
        """
        from orb.providers.base.strategy import ProviderOperation, ProviderOperationType

        # Create provider operation with dry-run context
        operation_context = {"skip_provisioning_port": True}
        if dry_run:
            operation_context["dry_run"] = True

        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": template.model_dump(),
                "count": request.requested_count,
                "request_id": str(request.request_id),
            },
            context=operation_context,
        )

        # Execute operation via provider strategy
        result = await self._provider_strategy.execute_operation(operation)

        if result.success:
            # Extract resource ID from result
            resource_id = result.data.get("instance_ids", ["dry-run-resource-id"])[0]
            self._logger.info(
                "Successfully provisioned resources via strategy with ID %s",
                resource_id,
            )
            return resource_id
        else:
            self._logger.error("Provider strategy operation failed: %s", result.error_message)
            raise InfrastructureError(f"Failed to provision resources: {result.error_message}")

    def _provision_via_handlers(
        self, request: Request, template: Template, dry_run: bool = False
    ) -> dict[str, Any]:
        """
        Provision resources using the legacy handler approach.

        Args:
            request: The request to fulfill
            template: The template to use for provisioning

        Returns:
            dict: The provisioning result with resource_ids list
        """
        if dry_run:
            self._logger.info(
                "Dry-run mode: skipping actual provisioning for template %s", template.template_id
            )
            return {"success": True, "resource_ids": [], "instances": [], "dry_run": True}

        # Get the appropriate handler for the template
        handler = self._get_handler_for_template(template)

        # Resolve SSM parameter paths to real AMI IDs before calling the handler
        template = self._resolve_template_image(template)

        # Convert domain Template to AWSTemplate so handlers can access AWS-specific fields
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

        if isinstance(template, AWSTemplate):
            aws_template = template
        else:
            aws_template = AWSTemplate.model_validate(template.model_dump())

        try:
            # Acquire hosts using the handler
            result = handler.acquire_hosts(request, aws_template)  # type: ignore[arg-type]

            # Handle both string (legacy) and dict (new) return types
            if isinstance(result, dict):
                success = result.get("success", True)
                if not success:
                    error_msg = result.get("error_message", "Handler reported failure")
                    raise InfrastructureError(f"Handler failed: {error_msg}")

                resource_ids = result.get("resource_ids", [])
                self._logger.info("Successfully provisioned resources with IDs %s", resource_ids)
                return result
            else:
                # Legacy string return - convert to new format
                resource_ids = [result] if result else []
                self._logger.info("Successfully provisioned resources with IDs %s", resource_ids)
                return {"success": True, "resource_ids": resource_ids, "instances": []}
        except AWSValidationError as e:
            self._logger.error("Validation error during resource provisioning: %s", str(e))
            raise
        except QuotaExceededError as e:
            self._logger.error("Quota exceeded during resource provisioning: %s", str(e))
            raise
        except Exception as e:
            self._logger.error("Error during resource provisioning: %s", str(e))
            raise InfrastructureError(str(e))

    def _resolve_template_image(self, template: Template) -> Template:
        """Resolve SSM parameter paths in template.image_id to real AMI IDs."""
        image_id = template.image_id
        if not image_id:
            return template

        try:
            from orb.providers.aws.infrastructure.caching.aws_image_cache import AWSImageCache
            from orb.providers.aws.infrastructure.services.aws_image_resolution_service import (
                AWSImageResolutionService,
            )

            cache_dir = self._config_port.get_cache_dir() if self._config_port else ""

            cache = AWSImageCache(
                provider_name="aws",
                cache_dir=cache_dir,
                ttl_seconds=3600,
            )
            service = AWSImageResolutionService(
                aws_client=self._aws_client,
                cache=cache,
                logger=self._logger,
            )

            if service.is_resolution_needed(image_id):
                resolved = service.resolve_image_id(image_id)
                self._logger.info("Resolved image_id %s -> %s", image_id, resolved)
                return template.update_image_id(resolved)
        except Exception as e:
            self._logger.error("Failed to resolve image_id '%s': %s", image_id, e)
            raise InfrastructureError(
                f"Failed to resolve AMI ID for image_id '{image_id}': {e}. "
                "Ensure the SSM parameter path is valid and the IAM role has ssm:GetParameter permission."
            )

        return template

    def release_resources(
        self,
        machine_ids: list[str],
        template_id: str,
        provider_api: str,
        context: dict = None,  # type: ignore[assignment]
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
        request_id: str = "",
    ) -> None:
        """
        Release provisioned AWS resources using direct parameters.

        Args:
            machine_ids: List of instance IDs to terminate
            template_id: Template ID used to create the instances
            provider_api: Provider API type (ASG, EC2Fleet, SpotFleet, RunInstances)
            context: Context dictionary (unused in new flow)
            resource_mapping: Dict mapping instance_id -> (resource_id or None, desired_capacity)
            request_id: Original provisioning request ID, used for launch template cleanup

        Raises:
            AWSEntityNotFoundError: If the resource is not found
            InfrastructureError: For other infrastructure errors
        """
        context = context or {}
        resource_mapping = resource_mapping or {}

        self._logger.info(
            "Releasing resources: %d instances from template %s using %s handler (resource_mapping: %s)",
            len(machine_ids),
            template_id,  # KBG potentially remove alltogether.
            provider_api,
            len(resource_mapping),
        )

        if not machine_ids:
            self._logger.error("No instance IDs provided for resource release")
            raise AWSValidationError("Instance IDs are required for resource release")

        if not template_id:
            self._logger.error("No template ID provided for resource release")
            raise AWSValidationError("Template ID is required for resource release")

        # Get handler using caching helper method based on provider_api
        handler = self._get_handler_for_provider_api(provider_api)

        # Call handler's release_hosts method for all provider APIs
        try:
            handler.release_hosts(
                machine_ids, resource_mapping=resource_mapping, request_id=request_id
            )  # type: ignore[call-arg]

        except Exception as e:
            self._logger.error(
                "Failed to release resources using %s handler: %s", provider_api, str(e)
            )
            raise InfrastructureError(f"Failed to release {provider_api} resources: {e!s}")

        self._logger.info(
            "Successfully released %d instances using %s handler", len(machine_ids), provider_api
        )

    # No current caller — available for a future health endpoint
    def get_resource_health(self, resource_id: str) -> dict[str, Any]:
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
        self._logger.info("Getting health information for resource %s", resource_id)

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
                            "resource_type": "ASG",
                            "status": "active",
                            "desired_capacity": asg["DesiredCapacity"],
                            "current_capacity": len(asg["Instances"]),
                            "details": asg,
                        }
                except Exception as e:
                    self._logger.warning(
                        "Failed to process auto scaling group details: %s",
                        e,
                        extra={"resource_id": resource_id},
                    )

                # If we get here, we couldn't determine the resource type
                raise AWSEntityNotFoundError(
                    f"Resource {resource_id} not found or type not supported"
                )
        except AWSEntityNotFoundError:
            raise
        except Exception as e:
            self._logger.error("Error getting resource health: %s", str(e))
            raise InfrastructureError(f"Failed to get resource health: {e!s}")

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

        handler = self._provider_strategy.get_handler(handler_type)
        if not handler:
            raise AWSValidationError(f"No handler available for type: {handler_type}")

        self._handlers[handler_type] = handler
        return handler

    def _get_handler_for_provider_api(self, provider_api: str) -> AWSHandler:
        """
        Get the appropriate AWS handler for the provider API.

        Args:
            provider_api: The provider API type (ASG, EC2Fleet, SpotFleet, RunInstances)

        Returns:
            AWSHandler: The appropriate handler for the provider API

        Raises:
            ValidationError: If the provider API has an invalid handler type
        """
        # Check if we already have a cached handler for this type
        if provider_api in self._handlers:
            return self._handlers[provider_api]

        handler = self._provider_strategy.get_handler(provider_api)
        if not handler:
            raise AWSValidationError(f"No handler available for type: {provider_api}")

        self._handlers[provider_api] = handler
        return handler
