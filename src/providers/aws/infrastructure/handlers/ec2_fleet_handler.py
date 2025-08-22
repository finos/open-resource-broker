"""AWS EC2 Fleet Handler.

This module provides the EC2 Fleet handler implementation for managing
AWS EC2 Fleet requests through the AWS EC2 Fleet API.

The EC2 Fleet handler supports both On-Demand and Spot instance provisioning
with advanced fleet management capabilities including multiple instance types,
availability zones, and capacity optimization strategies.

Key Features:
    - Mixed instance type support
    - On-Demand and Spot instance combinations
    - Capacity optimization strategies
    - Multi-AZ deployment support
    - Advanced fleet configuration

Classes:
    EC2FleetHandler: Main handler for EC2 Fleet operations

Usage:
    This handler is used by the AWS provider to manage EC2 Fleet requests
    for complex deployment scenarios requiring advanced fleet management.

Note:
    EC2 Fleet provides more advanced capabilities than individual instance
    launches and is suitable for large-scale, complex deployments.
"""

from datetime import datetime
from typing import Any, Dict, List

from botocore.exceptions import ClientError

from application.dto.queries import GetTemplateQuery
from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.di.buses import QueryBus
from infrastructure.di.container import get_container
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.resilience import CircuitBreakerOpenError
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.domain.template.value_objects import AWSFleetType
from providers.aws.exceptions.aws_exceptions import (
    AWSEntityNotFoundError,
    AWSInfrastructureError,
    AWSValidationError,
)
from providers.aws.infrastructure.handlers.base_context_mixin import BaseContextMixin
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from providers.aws.utilities.aws_operations import AWSOperations
from infrastructure.utilities.common.resource_naming import get_resource_prefix


@injectable
class EC2FleetHandler(AWSHandler, BaseContextMixin):
    """Handler for EC2 Fleet operations."""

    def __init__(
        self,
        aws_client,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: RequestAdapterPort = None,
    ) -> None:
        """
        Initialize the EC2 Fleet handler.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            aws_ops: AWS operations utility
            launch_template_manager: Launch template manager for AWS-specific operations
            request_adapter: Optional request adapter for terminating instances
        """
        # Use base class initialization - eliminates duplication
        super().__init__(aws_client, logger, aws_ops, launch_template_manager, request_adapter)

        # Get AWS native spec service from container
        container = get_container()
        try:
            from providers.aws.infrastructure.services.aws_native_spec_service import (
                AWSNativeSpecService,
            )

            self.aws_native_spec_service = container.get(AWSNativeSpecService)
            # Get config port for package info
            from domain.base.ports.configuration_port import ConfigurationPort

            self.config_port = container.get(ConfigurationPort)
        except Exception:
            # Service not available, native specs disabled
            self.aws_native_spec_service = None
            self.config_port = None

    @handle_infrastructure_exceptions(context="ec2_fleet_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> Dict[str, Any]:
        """
        Create an EC2 Fleet to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            fleet_id = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_fleet_internal(request, aws_template),
                operation_name="create EC2 fleet",
                context="EC2Fleet",
            )

            # Get instance details based on fleet type
            instances = []
            if aws_template.fleet_type == "instant":
                # For instant fleets, instance IDs are in metadata
                instance_ids = request.metadata.get("instance_ids", [])
                if instance_ids:
                    instance_details = self._get_instance_details(instance_ids)
                    instances = self._format_instance_data(instance_details, fleet_id)

            return {
                "success": True,
                "resource_ids": [fleet_id],
                "instances": instances,
                "provider_data": {
                    "resource_type": "ec2_fleet",
                    "fleet_type": aws_template.fleet_type,
                },
            }
        except Exception as e:
            return {
                "success": False,
                "resource_ids": [],
                "instances": [],
                "error_message": str(e),
            }

    def _create_fleet_internal(self, request: Request, aws_template: AWSTemplate) -> str:
        """Create EC2 Fleet with pure business logic."""
        # Validate prerequisites
        self._validate_prerequisites(aws_template)

        # Validate fleet type
        if not aws_template.fleet_type:
            raise AWSValidationError("Fleet type is required for EC2Fleet")

        # Validate fleet type using existing validation system
        from providers.aws.infrastructure.adapters.aws_validation_adapter import (
            create_aws_validation_adapter,
        )

        validation_adapter = create_aws_validation_adapter(self._logger)
        valid_types = validation_adapter.get_valid_fleet_types_for_api("EC2Fleet")

        try:
            fleet_type = AWSFleetType(aws_template.fleet_type.lower())
            if fleet_type.value not in valid_types:
                raise ValueError  # Will be caught by the except block below
        except ValueError:
            raise AWSValidationError(
                f"Invalid EC2 fleet type: {aws_template.fleet_type}. "
                f"Must be one of: {', '.join(valid_types)}"
            )

        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
        )

        # Store launch template info in request (if request has this method)
        if hasattr(request, "set_launch_template_info"):
            request.set_launch_template_info(
                launch_template_result.template_id, launch_template_result.version
            )

        # Create fleet configuration
        fleet_config = self._create_fleet_config(
            template=aws_template,
            request=request,
            launch_template_id=launch_template_result.template_id,
            launch_template_version=launch_template_result.version,
        )

        # Create the fleet with circuit breaker for critical operation
        try:
            response = self._retry_with_backoff(
                self.aws_client.ec2_client.create_fleet,
                operation_type="critical",
                **fleet_config,
            )
        except CircuitBreakerOpenError as e:
            self._logger.error("Circuit breaker OPEN for EC2 Fleet creation: %s", str(e))
            # Re-raise to allow upper layers to handle graceful degradation
            raise

        fleet_id = response["FleetId"]
        self._logger.info("Successfully created EC2 Fleet: %s", fleet_id)

        # Apply post-creation tagging for fleet instances
        # EC2Fleet maintain/request types can't tag instances at creation - need post-creation
        if aws_template.fleet_type in ["maintain", "request"]:
            self._tag_fleet_instances_if_needed(fleet_id, request, aws_template)

        # For instant fleets, store instance IDs in request metadata
        if fleet_type == AWSFleetType.INSTANT:
            instance_ids = []
            # The correct field for instant fleets is 'fleetInstanceSet'
            for instance in response.get("fleetInstanceSet", []):
                if "InstanceId" in instance:
                    instance_ids.append(instance["InstanceId"])

            # Log the response structure at debug level if no instances were found
            if not instance_ids:
                self._logger.debug(
                    "No instance IDs found in response. Response structure: %s", response
                )

            request.metadata["instance_ids"] = instance_ids
            self._logger.debug("Stored instance IDs in request metadata: %s", instance_ids)

        return fleet_id

    def _format_instance_data(
        self, instance_details: List[Dict[str, Any]], resource_id: str
    ) -> List[Dict[str, Any]]:
        """Format AWS instance details to standard structure."""
        return [
            {
                "instance_id": inst["InstanceId"],
                "resource_id": resource_id,
                "status": inst["State"],
                "private_ip": inst.get("PrivateIpAddress"),
                "public_ip": inst.get("PublicIpAddress"),
                "launch_time": inst.get("LaunchTime"),
            }
            for inst in instance_details
        ]

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> Dict[str, Any]:
        """Prepare context with all computed values for template rendering."""

        # Start with base context
        context = self._prepare_base_context(template, request)

        # Add capacity distribution
        context.update(self._calculate_capacity_distribution(template, request))

        # Add standard flags
        context.update(self._prepare_standard_flags(template))

        # Add standard tags
        tag_context = self._prepare_standard_tags(template, request)
        context.update(tag_context)

        # Add EC2Fleet-specific context
        context.update(self._prepare_ec2fleet_specific_context(template, request))

        return context

    def _prepare_ec2fleet_specific_context(
        self, template: AWSTemplate, request: Request
    ) -> Dict[str, Any]:
        """Prepare EC2Fleet-specific context."""

        # Instance overrides computation
        instance_overrides = []
        if template.instance_types and template.subnet_ids:
            for subnet_id in template.subnet_ids:
                for instance_type, weight in template.instance_types.items():
                    instance_overrides.append(
                        {
                            "instance_type": instance_type,
                            "subnet_id": subnet_id,
                            "weighted_capacity": weight,
                        }
                    )
        elif template.instance_types:
            for instance_type, weight in template.instance_types.items():
                instance_overrides.append(
                    {"instance_type": instance_type, "weighted_capacity": weight}
                )

        # On-demand instance overrides for heterogeneous fleets
        ondemand_overrides = []
        if (
            template.price_type == "heterogeneous"
            and hasattr(template, "instance_types_ondemand")
            and template.instance_types_ondemand
        ):
            for instance_type, weight in template.instance_types_ondemand.items():
                ondemand_overrides.append(
                    {"instance_type": instance_type, "weighted_capacity": weight}
                )

        return {
            # Fleet-specific values
            "fleet_type": template.fleet_type,
            "fleet_name": f"{get_resource_prefix('fleet')}{request.request_id}",
            # Computed overrides
            "instance_overrides": instance_overrides,
            "ondemand_overrides": ondemand_overrides,
            "needs_overrides": bool(instance_overrides or ondemand_overrides),
            # Fleet-specific flags
            "is_maintain_fleet": template.fleet_type == AWSFleetType.MAINTAIN.value,
            "replace_unhealthy": template.fleet_type == AWSFleetType.MAINTAIN.value,
            "has_spot_options": bool(template.allocation_strategy or template.max_spot_price),
            "has_ondemand_options": bool(template.allocation_strategy_on_demand),
            # Configuration values
            "allocation_strategy": (
                self._get_allocation_strategy(template.allocation_strategy)
                if template.allocation_strategy
                else None
            ),
            "allocation_strategy_on_demand": (
                self._get_allocation_strategy_on_demand(template.allocation_strategy_on_demand)
                if template.allocation_strategy_on_demand
                else None
            ),
            "max_spot_price": (
                str(template.max_spot_price) if template.max_spot_price is not None else None
            ),
            "default_capacity_type": self._get_default_capacity_type(template.price_type),
        }

    def _get_default_capacity_type(self, price_type: str) -> str:
        """Get default target capacity type based on price type."""
        if price_type == "spot":
            return "spot"
        elif price_type == "ondemand":
            return "on-demand"
        else:  # heterogeneous or None
            return "on-demand"

    def _create_fleet_config(
        self,
        template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> Dict[str, Any]:
        """Create EC2 Fleet configuration with native spec support."""
        # Try native spec processing first
        if self.aws_native_spec_service:
            native_spec = self.aws_native_spec_service.process_provider_api_spec(template, request)
            if native_spec:
                # Merge launch template info into native spec
                if "LaunchTemplateConfigs" in native_spec:
                    native_spec["LaunchTemplateConfigs"][0]["LaunchTemplateSpecification"] = {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    }
                self._logger.info(
                    "Using native provider API spec for template %s", template.template_id
                )
                return native_spec

            # Use template-driven approach with native spec service
            context = self._prepare_template_context(template, request)
            context.update(
                {
                    "launch_template_id": launch_template_id,
                    "launch_template_version": launch_template_version,
                }
            )

            return self.aws_native_spec_service.render_default_spec("ec2fleet", context)

        # Fallback to legacy logic when native spec service is not available
        return self._create_fleet_config_legacy(
            template, request, launch_template_id, launch_template_version
        )

    def _create_fleet_config_legacy(
        self,
        template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> Dict[str, Any]:
        """Create EC2 Fleet configuration using legacy logic."""
        # Get package name for CreatedBy tag
        created_by = "open-hostfactory-plugin"  # fallback
        if hasattr(self, "config_port") and self.config_port:
            try:
                package_info = self.config_port.get_package_info()
                created_by = package_info.get("name", "open-hostfactory-plugin")
            except Exception:  # nosec B110
                # Intentionally silent fallback for package info retrieval
                pass

        fleet_config = {
            "LaunchTemplateConfigs": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    }
                }
            ],
            "TargetCapacitySpecification": {"TotalTargetCapacity": request.requested_count},
            "Type": template.fleet_type,
            "TagSpecifications": [
                {
                    "ResourceType": "fleet",
                    "Tags": [
                        {"Key": "Name", "Value": f"{get_resource_prefix('fleet')}{request.request_id}"},
                        {"Key": "RequestId", "Value": str(request.request_id)},
                        {"Key": "TemplateId", "Value": str(template.template_id)},
                        {"Key": "CreatedBy", "Value": created_by},
                        {"Key": "CreatedAt", "Value": datetime.utcnow().isoformat()},
                    ],
                }
            ],
        }

        # Add template tags if any
        if template.tags:
            fleet_tags = [{"Key": k, "Value": v} for k, v in template.tags.items()]
            fleet_config["TagSpecifications"][0]["Tags"].extend(fleet_tags)

        # Add fleet type specific configurations
        if template.fleet_type == AWSFleetType.MAINTAIN.value:
            fleet_config["ReplaceUnhealthyInstances"] = True
            fleet_config["ExcessCapacityTerminationPolicy"] = "termination"

        # Configure pricing type
        price_type = template.price_type or "ondemand"
        if price_type == "ondemand":
            fleet_config["TargetCapacitySpecification"]["DefaultTargetCapacityType"] = "on-demand"
        elif price_type == "spot":
            fleet_config["TargetCapacitySpecification"]["DefaultTargetCapacityType"] = "spot"

            # Add allocation strategy if specified
            if template.allocation_strategy:
                fleet_config["SpotOptions"] = {
                    "AllocationStrategy": self._get_allocation_strategy(
                        template.allocation_strategy
                    )
                }

            # Add max spot price if specified
            if template.max_spot_price is not None:
                if "SpotOptions" not in fleet_config:
                    fleet_config["SpotOptions"] = {}
                fleet_config["SpotOptions"]["MaxTotalPrice"] = str(template.max_spot_price)
        elif price_type == "heterogeneous":
            # For heterogeneous fleets, we need to specify both on-demand and spot
            # capacities
            percent_on_demand = template.percent_on_demand or 0
            on_demand_count = int(request.requested_count * percent_on_demand / 100)
            spot_count = request.requested_count - on_demand_count

            fleet_config["TargetCapacitySpecification"]["OnDemandTargetCapacity"] = on_demand_count
            fleet_config["TargetCapacitySpecification"]["SpotTargetCapacity"] = spot_count
            fleet_config["TargetCapacitySpecification"]["DefaultTargetCapacityType"] = "on-demand"

            # Add allocation strategies if specified
            if template.allocation_strategy:
                fleet_config["SpotOptions"] = {
                    "AllocationStrategy": self._get_allocation_strategy(
                        template.allocation_strategy
                    )
                }

            if template.allocation_strategy_on_demand:
                fleet_config["OnDemandOptions"] = {
                    "AllocationStrategy": self._get_allocation_strategy_on_demand(
                        template.allocation_strategy_on_demand
                    )
                }

            # Add max spot price if specified
            if template.max_spot_price is not None:
                if "SpotOptions" not in fleet_config:
                    fleet_config["SpotOptions"] = {}
                fleet_config["SpotOptions"]["MaxTotalPrice"] = str(template.max_spot_price)

        # Add overrides with weighted capacity if multiple instance types are specified
        if template.instance_types:
            overrides = []
            for instance_type, weight in template.instance_types.items():
                override = {"InstanceType": instance_type, "WeightedCapacity": weight}
                overrides.append(override)
            fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides

            # Add on-demand instance types for heterogeneous fleets
            if price_type == "heterogeneous" and template.instance_types_ondemand:
                on_demand_overrides = []
                for instance_type, weight in template.instance_types_ondemand.items():
                    override = {
                        "InstanceType": instance_type,
                        "WeightedCapacity": weight,
                    }
                    on_demand_overrides.append(override)

                # Add on-demand overrides to the existing overrides
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"].extend(on_demand_overrides)

        # Add subnet configuration
        if template.subnet_ids:
            if "Overrides" not in fleet_config["LaunchTemplateConfigs"][0]:
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = []

            # If we have both instance types and subnets, create all combinations
            if template.instance_types:
                overrides = []
                for subnet_id in template.subnet_ids:
                    for instance_type, weight in template.instance_types.items():
                        override = {
                            "SubnetId": subnet_id,
                            "InstanceType": instance_type,
                            "WeightedCapacity": weight,
                        }
                        overrides.append(override)

                    # Add on-demand instance types for heterogeneous fleets
                    if price_type == "heterogeneous" and template.instance_types_ondemand:
                        for (
                            instance_type,
                            weight,
                        ) in template.instance_types_ondemand.items():
                            override = {
                                "SubnetId": subnet_id,
                                "InstanceType": instance_type,
                                "WeightedCapacity": weight,
                            }
                            overrides.append(override)

                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides
            else:
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = [
                    {"SubnetId": subnet_id} for subnet_id in template.subnet_ids
                ]

        # Add Context field if specified
        if template.context:
            fleet_config["Context"] = template.context

        return fleet_config

    def _get_allocation_strategy(self, strategy: str) -> str:
        """Convert Symphony allocation strategy to EC2 Fleet allocation strategy."""
        strategy_map = {
            "capacityOptimized": "capacity-optimized",
            "capacityOptimizedPrioritized": "capacity-optimized-prioritized",
            "diversified": "diversified",
            "lowestPrice": "lowest-price",
            "priceCapacityOptimized": "price-capacity-optimized",
        }

        return strategy_map.get(strategy, "lowest-price")

    def _get_allocation_strategy_on_demand(self, strategy: str) -> str:
        """Convert Symphony on-demand allocation strategy to EC2 Fleet allocation strategy."""
        strategy_map = {"lowestPrice": "lowest-price", "prioritized": "prioritized"}

        return strategy_map.get(strategy, "lowest-price")

    async def check_hosts_status(self, request: Request) -> List[Dict[str, Any]]:
        """Check the status of instances in the fleet."""
        try:
            if not request.resource_ids:
                raise AWSInfrastructureError("No Fleet ID found in request")

            fleet_id = request.resource_ids[0]  # Use first resource ID as fleet ID

            # Get template using CQRS QueryBus
            container = get_container()
            query_bus = container.get(QueryBus)
            if not query_bus:
                raise AWSInfrastructureError("QueryBus not available")

            query = GetTemplateQuery(template_id=str(request.template_id))
            template = await query_bus.execute(query)
            if not template:
                raise AWSEntityNotFoundError(f"Template {request.template_id} not found")

            # Ensure fleet_type is not None
            fleet_type_value = template.metadata.get("aws", {}).get("fleet_type", "instant")
            if not fleet_type_value:
                raise AWSValidationError("Fleet type is required")

            fleet_type = AWSFleetType(fleet_type_value.lower())

            # Get fleet information with pagination and retry
            fleet_list = self._retry_with_backoff(
                lambda: self._paginate(
                    self.aws_client.ec2_client.describe_fleets,
                    "Fleets",
                    FleetIds=[fleet_id],
                ),
                operation_type="read_only",
            )

            if not fleet_list:
                raise AWSEntityNotFoundError(f"Fleet {fleet_id} not found")

            fleet = fleet_list[0]

            # Log fleet status
            self._logger.debug(
                "Fleet status: %s, Target capacity: %s, Fulfilled capacity: %s",
                fleet.get("Status"),
                fleet.get("TargetCapacitySpecification", {}).get("TotalTargetCapacity"),
                fleet.get("FulfilledCapacity", 0),
            )

            # Get instance IDs based on fleet type
            instance_ids = []
            if fleet_type == AWSFleetType.INSTANT:
                # For instant fleets, get instance IDs from metadata
                instance_ids = request.metadata.get("instance_ids", [])
            else:
                # For request/maintain fleets, describe fleet instances with pagination
                # and retry
                active_instances = self._retry_with_backoff(
                    lambda: self._paginate(
                        self.aws_client.ec2_client.describe_fleet_instances,
                        "ActiveInstances",
                        FleetId=fleet_id,
                    ),
                    operation_type="read_only",
                )
                instance_ids = [instance["InstanceId"] for instance in active_instances]

            if not instance_ids:
                self._logger.info("No active instances found in fleet %s", fleet_id)
                return []

            # Get detailed instance information
            return self._get_instance_details(instance_ids)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to check EC2 Fleet status: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error checking EC2 Fleet status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check EC2 Fleet status: {str(e)}")

    def release_hosts(self, request: Request) -> None:
        """
        Release specific hosts or entire EC2 Fleet.

        Args:
            request: The request containing the fleet and machine information
        """
        try:
            if not request.resource_ids:
                raise AWSInfrastructureError("No EC2 Fleet ID found in request")

            fleet_id = request.resource_ids[0]  # Use first resource ID as fleet ID

            # Get fleet configuration with pagination and retry
            fleet_list = self._retry_with_backoff(
                lambda: self._paginate(
                    self.aws_client.ec2_client.describe_fleets,
                    "Fleets",
                    FleetIds=[fleet_id],
                ),
                operation_type="read_only",
            )

            if not fleet_list:
                raise AWSEntityNotFoundError(f"EC2 Fleet {fleet_id} not found")

            fleet = fleet_list[0]
            fleet_type = fleet.get("Type", "maintain")

            # Get instance IDs from machine references
            instance_ids = []
            if request.machine_references:
                instance_ids = [m.machine_id for m in request.machine_references]

            if instance_ids:
                if fleet_type == "maintain":
                    # For maintain fleets, reduce target capacity first
                    current_capacity = fleet["TargetCapacitySpecification"]["TotalTargetCapacity"]
                    new_capacity = max(0, current_capacity - len(instance_ids))

                    self._retry_with_backoff(
                        self.aws_client.ec2_client.modify_fleet,
                        operation_type="critical",
                        FleetId=fleet_id,
                        TargetCapacitySpecification={"TotalTargetCapacity": new_capacity},
                    )
                    self._logger.info(
                        "Reduced maintain fleet %s capacity to %s", fleet_id, new_capacity
                    )

                # Use consolidated AWS operations utility for instance termination
                self.aws_ops.terminate_instances_with_fallback(
                    instance_ids, self._request_adapter, "EC2 Fleet instances"
                )
            else:
                # Delete entire fleet
                self._retry_with_backoff(
                    self.aws_client.ec2_client.delete_fleets,
                    operation_type="critical",
                    FleetIds=[fleet_id],
                    TerminateInstances=True,
                )
                self._logger.info("Deleted EC2 Fleet: %s", fleet_id)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to release EC2 Fleet resources: %s", str(error))
            raise error
