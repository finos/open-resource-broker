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

import json
from datetime import datetime
from typing import Any, Optional

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
from infrastructure.utilities.common.resource_naming import get_resource_prefix
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.domain.template.value_objects import AWSFleetType
from providers.aws.exceptions.aws_exceptions import (
    AWSEntityNotFoundError,
    AWSInfrastructureError,
    AWSValidationError,
)
from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.handlers.base_context_mixin import BaseContextMixin
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from providers.aws.utilities.aws_operations import AWSOperations


@injectable
class EC2FleetHandler(AWSHandler, BaseContextMixin):
    """Handler for EC2 Fleet operations."""

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: RequestAdapterPort = None,
        machine_adapter: Optional[AWSMachineAdapter] = None,
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
        super().__init__(
            aws_client,
            logger,
            aws_ops,
            launch_template_manager,
            request_adapter,
            machine_adapter,
        )

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

            if self._machine_adapter is None:
                from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter

                self._machine_adapter = container.get(AWSMachineAdapter)
        except Exception:
            # Service not available, native specs disabled
            self.aws_native_spec_service = None
            self.config_port = None

    @handle_infrastructure_exceptions(context="ec2_fleet_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> dict[str, Any]:
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
            instances: list[dict[str, Any]] = []
            instance_details: list[dict[str, Any]] = []
            if aws_template.fleet_type == "instant":
                # For instant fleets, instance IDs are in metadata
                instance_ids = request.metadata.get("instance_ids", [])
                if instance_ids:
                    instance_details = self._get_instance_details(
                        instance_ids,
                        request_id=str(request.request_id),
                        resource_id=fleet_id,
                        provider_api="EC2Fleet",
                    )

            if instance_details:
                instances = self._format_instance_data(
                    instance_details, fleet_id, request, aws_template
                )

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
            self._logger.debug(
                "AWS EC2 Fleet create fleet payload:\n%s",
                json.dumps(fleet_config, default=str, indent=2, sort_keys=True),
            )
            response = self._retry_with_backoff(
                self.aws_client.ec2_client.create_fleet,
                operation_type="critical",
                **fleet_config,
            )
            self._logger.debug(
                "EC2 Fleet create_fleet response:\n%s",
                json.dumps(response, default=str, indent=2, sort_keys=True),
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
            # For instant fleets, AWS returns 'Instances' -> [{ 'InstanceIds': ['i-...', 'i-...'], ... }]
            for inst_block in response.get("Instances", []):
                for instance_id in inst_block.get("InstanceIds", []):
                    instance_ids.append(instance_id)

            # Log the response structure at debug level if no instances were found
            if not instance_ids:
                self._logger.debug(
                    "No instance IDs found in response. Response structure: %s",
                    response,
                )

            request.metadata["instance_ids"] = instance_ids
            self._logger.debug("Stored instance IDs in request metadata: %s", instance_ids)

        return fleet_id

    def _format_instance_data(
        self,
        instance_details: list[dict[str, Any]],
        resource_id: str,
        request: Request,
        aws_template: Optional[AWSTemplate] = None,
    ) -> list[dict[str, Any]]:
        """Format AWS instance details to standard structure."""
        metadata = getattr(request, "metadata", {}) or {}
        if aws_template and aws_template.provider_api is not None:
            provider_api_value = (
                aws_template.provider_api.value
                if hasattr(aws_template.provider_api, "value")
                else str(aws_template.provider_api)
            )
        else:
            provider_api_value = metadata.get("provider_api", "EC2Fleet")

        if self._machine_adapter:
            try:
                return [
                    self._machine_adapter.create_machine_from_aws_instance(
                        inst,
                        request_id=str(request.request_id),
                        provider_api=provider_api_value,
                        resource_id=resource_id,
                    )
                    for inst in instance_details
                ]
            except Exception as exc:
                self._logger.error("Failed to normalize instances with machine adapter: %s", exc)
                raise AWSInfrastructureError(
                    "Failed to normalize instance data with AWS machine adapter"
                ) from exc

        return [
            self._build_fallback_machine_payload(inst, resource_id) for inst in instance_details
        ]

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> dict[str, Any]:
        """Prepare context with all computed values for template rendering."""

        # Start with base context
        context = self._prepare_base_context(
            template,
            str(request.request_id),
            request.requested_count,
        )

        # Add capacity distribution
        context.update(self._calculate_capacity_distribution(template, request.requested_count))

        # Add standard flags
        context.update(self._prepare_standard_flags(template))

        # Add standard tags
        tag_context = self._prepare_standard_tags(template, str(request.request_id))
        context.update(tag_context)

        # Add EC2Fleet-specific context
        context.update(self._prepare_ec2fleet_specific_context(template, request))

        return context

    def _prepare_ec2fleet_specific_context(
        self, template: AWSTemplate, request: Request
    ) -> dict[str, Any]:
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
            "fleet_type": template.fleet_type.value,
            "fleet_name": f"{get_resource_prefix('fleet')}{request.request_id}",
            # Computed overrides
            "instance_overrides": instance_overrides,
            "ondemand_overrides": ondemand_overrides,
            "needs_overrides": bool(instance_overrides or ondemand_overrides),
            # Fleet-specific flags
            "is_maintain_fleet": template.fleet_type == AWSFleetType.MAINTAIN.value,
            "replace_unhealthy": template.fleet_type == AWSFleetType.MAINTAIN.value,
            "has_spot_options": bool(template.allocation_strategy or template.max_price),
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
            "max_spot_price": (str(template.max_price) if template.max_price is not None else None),
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
    ) -> dict[str, Any]:
        """Create EC2 Fleet configuration with native spec support."""
        # Try native spec processing with merge support
        if self.aws_native_spec_service:
            context = self._prepare_template_context(template, request)

            context.update(
                {
                    "launch_template_id": launch_template_id,
                    "launch_template_version": launch_template_version,
                }
            )

            native_spec = self.aws_native_spec_service.process_provider_api_spec_with_merge(
                template, request, "ec2fleet", context
            )
            if native_spec:
                # Ensure launch template info is in the spec
                if "LaunchTemplateConfigs" in native_spec:
                    native_spec["LaunchTemplateConfigs"][0]["LaunchTemplateSpecification"] = {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    }
                self._logger.info(
                    "Using native provider API spec with merge for template %s",
                    template.template_id,
                )
                return native_spec

            # Use template-driven approach with native spec service
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
    ) -> dict[str, Any]:
        """Create EC2 Fleet configuration using legacy logic."""
        # Get package name for CreatedBy tag
        created_by = self._get_package_name()

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
            "Type": template.fleet_type.value
            if hasattr(template.fleet_type, "value")
            else str(template.fleet_type),
            "TagSpecifications": [
                {
                    "ResourceType": "fleet",
                    "Tags": [
                        {
                            "Key": "Name",
                            "Value": f"{get_resource_prefix('fleet')}{request.request_id}",
                        },
                        {"Key": "RequestId", "Value": str(request.request_id)},
                        {"Key": "TemplateId", "Value": str(template.template_id)},
                        {"Key": "CreatedBy", "Value": created_by},
                        {"Key": "CreatedAt", "Value": datetime.utcnow().isoformat()},
                        {"Key": "ProviderApi", "Value": "EC2Fleet"},
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
            if template.max_price is not None:
                if "SpotOptions" not in fleet_config:
                    fleet_config["SpotOptions"] = {}
                fleet_config["SpotOptions"]["MaxTotalPrice"] = str(template.max_price)
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
            if template.max_price is not None:
                if "SpotOptions" not in fleet_config:
                    fleet_config["SpotOptions"] = {}
                fleet_config["SpotOptions"]["MaxTotalPrice"] = str(template.max_price)

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

    async def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Check the status of instances in the fleet."""
        try:
            self._logger.debug(f" check_hosts_status {request}")
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
            self._logger.debug(f" check_hosts_status template: {template}")
            self._logger.debug(f" check_hosts_status template.metadata: {template.metadata}")

            # Get fleet_type directly from template attribute (primary) or metadata (fallback)
            fleet_type_value = None

            # First, try template.fleet_type attribute (this should be the primary source)
            if hasattr(template, "fleet_type") and template.fleet_type:
                fleet_type_value = template.fleet_type
                self._logger.debug(
                    f" check_hosts_status fleet_type_value from template.fleet_type: {fleet_type_value} (type: {type(fleet_type_value)})"
                )

            # Fallback: check metadata directly (no "aws" nesting)
            if not fleet_type_value and template.metadata:
                fleet_type_value = template.metadata.get("fleet_type")
                self._logger.debug(
                    f" check_hosts_status fleet_type_value from metadata: {fleet_type_value}"
                )

            # If still not found, this is an error - don't default to instant
            if not fleet_type_value:
                raise AWSValidationError("Fleet type is required and not found in template")

            fleet_type = AWSFleetType(fleet_type_value.lower())
            self._logger.debug(f" check_hosts_status final fleet_type: {fleet_type}")

            # Get fleet information with pagination and retry
            fleet_list = self._retry_with_backoff(
                lambda: self._paginate(
                    self.aws_client.ec2_client.describe_fleets,
                    "Fleets",
                    FleetIds=[fleet_id],
                ),
                operation_type="read_only",
            )

            self._logger.debug(
                f" check_hosts_status fleet_type [{fleet_type}] [type: {type(fleet_list[0])}]fleet_list: {fleet_list}"
            )

            if not fleet_list:
                raise AWSEntityNotFoundError(f"Fleet {fleet_id} not found")

            fleet = fleet_list[0]

            # Log fleet status
            self._logger.debug(
                "Fleet status: %s, Target capacity: %s, Fulfilled capacity: %s",
                fleet.get("FleetState"),
                fleet.get("TargetCapacitySpecification", {}).get("TotalTargetCapacity"),
                fleet.get("FulfilledCapacity", 0),
            )

            # Get instance IDs based on fleet type
            instance_ids = []
            if fleet_type == AWSFleetType.INSTANT:
                # For instant fleets, get instance IDs from metadata
                instance_ids = request.metadata.get("instance_ids", [])
            else:
                # For request/maintain fleets, describe fleet instances with manual
                # pagination support
                active_instances = self._retry_with_backoff(
                    lambda: self._collect_with_next_token(
                        self.aws_client.ec2_client.describe_fleet_instances,
                        "ActiveInstances",
                        FleetId=fleet_id,
                    ),
                    operation_type="read_only",
                )
                instance_ids = [instance["InstanceId"] for instance in active_instances]
                self._logger.debug(
                    f" check_hosts_status instance_ids: {fleet_id} :: {instance_ids}"
                )

            if not instance_ids:
                self._logger.info("No active instances found in fleet %s", fleet_id)
                return []

            # Get detailed instance information
            instance_details = self._get_instance_details(
                instance_ids,
                request_id=str(request.request_id),
                resource_id=fleet_id,
                provider_api="EC2Fleet",
            )
            return self._format_instance_data(instance_details, fleet_id, request, template)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to check EC2 Fleet status: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error checking EC2 Fleet status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check EC2 Fleet status: {e!s}")

    def release_hosts(
        self, machine_ids: list[str], resource_mapping: list[tuple[str, str, int]] = None
    ) -> None:
        """Release hosts across multiple EC2 Fleets by detecting fleet membership.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: List of tuples (instance_id, resource_id or None, desired_capacity) for intelligent resource management
        """
        try:
            if not machine_ids:
                self._logger.warning("No instance IDs provided for EC2 Fleet termination")
                return

            self._logger.info("Releasing hosts for %d instances: %s", len(machine_ids), machine_ids)

            # Use resource_mapping if available, otherwise fall back to AWS API calls
            if resource_mapping:
                fleet_instance_groups = self._group_instances_by_ec2_fleet_from_mapping(
                    machine_ids, resource_mapping
                )
                self._logger.info(
                    f"Grouped instances by EC2 Fleet using resource mapping: {fleet_instance_groups}"
                )
            else:
                # Fallback to AWS API calls when no resource mapping is provided
                self._logger.info("No resource mapping provided, falling back to AWS API calls")
                fleet_instance_groups = self._group_instances_by_ec2_fleet(machine_ids)
                self._logger.info(
                    f"Grouped instances by EC2 Fleet using AWS API: {fleet_instance_groups}"
                )

            # Process each EC2 Fleet group separately
            for fleet_id, fleet_data in fleet_instance_groups.items():
                if fleet_id is not None:
                    # Handle EC2 Fleet instances using dedicated method (primary case)
                    self._release_hosts_for_single_ec2_fleet(
                        fleet_id, fleet_data["instance_ids"], fleet_data["fleet_details"]
                    )
                else:
                    # Handle non-EC2 Fleet instances (fallback case)
                    instance_ids = fleet_data["instance_ids"]
                    if instance_ids:
                        self._logger.info(
                            f"Terminating {len(instance_ids)} non-EC2 Fleet instances"
                        )
                        self.aws_ops.terminate_instances_with_fallback(
                            instance_ids, self._request_adapter, "non-EC2 Fleet instances"
                        )
                        self._logger.info("Terminated non-EC2 Fleet instances: %s", instance_ids)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to release EC2 Fleet resources: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Failed to release EC2 Fleet hosts: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release EC2 Fleet hosts: {e!s}")

    def _group_instances_by_ec2_fleet_from_mapping(
        self, machine_ids: list[str], resource_mapping: list[tuple[str, str, int]]
    ) -> dict[Optional[str], dict]:
        """
        Group instances by their EC2 Fleet membership using resource_mapping data.
        Only makes AWS API calls when resource_mapping doesn't have the necessary information.

        Args:
            machine_ids: List of EC2 instance IDs
            resource_mapping: List of tuples (instance_id, resource_id or None, desired_capacity)

        Returns:
            Dictionary mapping EC2 Fleet IDs to fleet details dict containing:
            - 'instance_ids': list of instance IDs
            - 'fleet_details': full EC2 Fleet configuration (for EC2 Fleet instances only)
            Non-EC2 Fleet instances are grouped under None key with only 'instance_ids'.
        """
        fleet_groups: dict[Optional[str], dict] = {}
        instances_needing_lookup = []
        fleet_ids_to_fetch = set()

        # Create a mapping for quick lookup
        resource_map = {
            instance_id: (resource_id, desired_capacity)
            for instance_id, resource_id, desired_capacity in resource_mapping
        }

        self._logger.info(f"Processing {len(machine_ids)} instances using resource mapping")

        # First pass: use resource_mapping data
        for instance_id in machine_ids:
            if instance_id in resource_map:
                resource_id, desired_capacity = resource_map[instance_id]

                if resource_id and desired_capacity > 0:
                    # We have EC2 Fleet information from resource_mapping
                    fleet_id = resource_id
                    if fleet_id not in fleet_groups:
                        fleet_groups[fleet_id] = {"instance_ids": [], "fleet_details": None}
                    fleet_groups[fleet_id]["instance_ids"].append(instance_id)
                    fleet_ids_to_fetch.add(fleet_id)

                    self._logger.debug(
                        f"Instance {instance_id} mapped to EC2 Fleet {fleet_id} from resource mapping"
                    )
                elif resource_id is None or desired_capacity == 0:
                    # Resource mapping indicates this is not an EC2 Fleet instance
                    if None not in fleet_groups:
                        fleet_groups[None] = {"instance_ids": []}
                    fleet_groups[None]["instance_ids"].append(instance_id)

                    self._logger.debug(
                        f"Instance {instance_id} marked as non-EC2 Fleet from resource mapping"
                    )
                else:
                    # Resource mapping has incomplete information, need AWS API lookup
                    instances_needing_lookup.append(instance_id)
                    self._logger.debug(
                        f"Instance {instance_id} needs AWS API lookup (incomplete resource mapping)"
                    )
            else:
                # Instance not in resource_mapping, need AWS API lookup
                instances_needing_lookup.append(instance_id)
                self._logger.debug(
                    f"Instance {instance_id} not in resource mapping, needs AWS API lookup"
                )

        # Second pass: AWS API lookup for instances with missing/incomplete information
        if instances_needing_lookup:
            self._logger.info(
                f"Making AWS API calls for {len(instances_needing_lookup)} instances with incomplete resource mapping"
            )

            try:
                for chunk in self._chunk_list(instances_needing_lookup, 50):
                    try:
                        response = self._retry_with_backoff(
                            self.aws_client.ec2_client.describe_instances,
                            operation_type="read_only",
                            InstanceIds=chunk,
                        )

                        # Track which instances were found in EC2 Fleets
                        ec2_fleet_instance_ids = set()

                        # Group instances by EC2 Fleet
                        for reservation in response.get("Reservations", []):
                            for instance in reservation.get("Instances", []):
                                instance_id = instance.get("InstanceId")
                                if not instance_id:
                                    continue

                                # Check if instance has EC2 Fleet ID in tags or metadata
                                ec2_fleet_id = None

                                # Check tags for EC2 Fleet ID
                                for tag in instance.get("Tags", []):
                                    if tag.get("Key") == "aws:ec2:fleet-id":
                                        ec2_fleet_id = tag.get("Value")
                                        break

                                # If not found in tags, try to find the fleet by querying all active fleets
                                if not ec2_fleet_id:
                                    ec2_fleet_id = self._find_ec2_fleet_for_instance(instance_id)

                                if ec2_fleet_id:
                                    if ec2_fleet_id not in fleet_groups:
                                        fleet_groups[ec2_fleet_id] = {
                                            "instance_ids": [],
                                            "fleet_details": None,
                                        }
                                    fleet_groups[ec2_fleet_id]["instance_ids"].append(instance_id)
                                    ec2_fleet_instance_ids.add(instance_id)
                                    fleet_ids_to_fetch.add(ec2_fleet_id)

                        # Add non-EC2 Fleet instances to None group
                        non_ec2_fleet_instances = [
                            iid for iid in chunk if iid not in ec2_fleet_instance_ids
                        ]
                        if non_ec2_fleet_instances:
                            if None not in fleet_groups:
                                fleet_groups[None] = {"instance_ids": []}
                            fleet_groups[None]["instance_ids"].extend(non_ec2_fleet_instances)

                    except Exception as e:
                        self._logger.warning(f"Failed to describe instances for chunk {chunk}: {e}")
                        # Add all instances in this chunk to non-EC2 Fleet group as fallback
                        if None not in fleet_groups:
                            fleet_groups[None] = {"instance_ids": []}
                        fleet_groups[None]["instance_ids"].extend(chunk)

            except Exception as e:
                self._logger.error(f"Failed to lookup EC2 Fleet information for instances: {e}")
                # Fallback: treat lookup instances as non-EC2 Fleet
                if None not in fleet_groups:
                    fleet_groups[None] = {"instance_ids": []}
                fleet_groups[None]["instance_ids"].extend(instances_needing_lookup)

        # Third pass: fetch EC2 Fleet details only for fleets we need
        if fleet_ids_to_fetch:
            self._logger.info(f"Fetching EC2 Fleet details for {len(fleet_ids_to_fetch)} fleets")
            try:
                fleet_ids_list = list(fleet_ids_to_fetch)
                for fleet_chunk in self._chunk_list(fleet_ids_list, 50):
                    fleet_response = self._retry_with_backoff(
                        self.aws_client.ec2_client.describe_fleets,
                        operation_type="read_only",
                        FleetIds=fleet_chunk,
                    )

                    for fleet_details in fleet_response.get("Fleets", []):
                        fleet_id = fleet_details.get("FleetId")
                        if fleet_id in fleet_groups:
                            fleet_groups[fleet_id]["fleet_details"] = fleet_details

            except Exception as e:
                self._logger.warning(f"Failed to fetch EC2 Fleet details: {e}")
                # Continue without fleet details - methods will handle missing details

        self._logger.info(
            f"Grouped {len(machine_ids)} instances into {len(fleet_groups)} groups using optimized resource mapping"
        )
        self._logger.info(
            f"AWS API calls avoided for {len(machine_ids) - len(instances_needing_lookup)} instances"
        )

        return fleet_groups

    def _group_instances_by_ec2_fleet(self, instance_ids: list[str]) -> dict[Optional[str], dict]:
        """
        Group instances by their EC2 Fleet membership and return full fleet details.

        Args:
            instance_ids: List of EC2 instance IDs

        Returns:
            Dictionary mapping EC2 Fleet IDs to fleet details dict containing:
            - 'instance_ids': list of instance IDs
            - 'fleet_details': full EC2 Fleet configuration (for EC2 Fleet instances only)
            Non-EC2 Fleet instances are grouped under None key with only 'instance_ids'.
        """
        fleet_groups: dict[Optional[str], dict] = {}
        fleet_ids_to_fetch = set()

        try:
            # First, group instances by EC2 Fleet ID
            for chunk in self._chunk_list(instance_ids, 50):
                try:
                    response = self._retry_with_backoff(
                        self.aws_client.ec2_client.describe_instances,
                        operation_type="read_only",
                        InstanceIds=chunk,
                    )

                    # Track which instances were found in EC2 Fleets
                    ec2_fleet_instance_ids = set()

                    # Group instances by EC2 Fleet
                    for reservation in response.get("Reservations", []):
                        for instance in reservation.get("Instances", []):
                            instance_id = instance.get("InstanceId")
                            if not instance_id:
                                continue

                            # Check if instance has EC2 Fleet ID in tags or metadata
                            ec2_fleet_id = None

                            # Check tags for EC2 Fleet ID
                            for tag in instance.get("Tags", []):
                                if tag.get("Key") == "aws:ec2:fleet-id":
                                    ec2_fleet_id = tag.get("Value")
                                    break

                            # If not found in tags, try to find the fleet by querying all active fleets
                            if not ec2_fleet_id:
                                ec2_fleet_id = self._find_ec2_fleet_for_instance(instance_id)

                            if ec2_fleet_id:
                                if ec2_fleet_id not in fleet_groups:
                                    fleet_groups[ec2_fleet_id] = {
                                        "instance_ids": [],
                                        "fleet_details": None,
                                    }
                                fleet_groups[ec2_fleet_id]["instance_ids"].append(instance_id)
                                ec2_fleet_instance_ids.add(instance_id)
                                fleet_ids_to_fetch.add(ec2_fleet_id)

                    # Add non-EC2 Fleet instances to None group
                    non_ec2_fleet_instances = [
                        iid for iid in chunk if iid not in ec2_fleet_instance_ids
                    ]
                    if non_ec2_fleet_instances:
                        if None not in fleet_groups:
                            fleet_groups[None] = {"instance_ids": []}
                        fleet_groups[None]["instance_ids"].extend(non_ec2_fleet_instances)

                except Exception as e:
                    self._logger.warning(f"Failed to describe instances for chunk {chunk}: {e}")
                    # Add all instances in this chunk to non-EC2 Fleet group as fallback
                    if None not in fleet_groups:
                        fleet_groups[None] = {"instance_ids": []}
                    fleet_groups[None]["instance_ids"].extend(chunk)

            # Now fetch EC2 Fleet details for all identified fleets
            if fleet_ids_to_fetch:
                try:
                    fleet_ids_list = list(fleet_ids_to_fetch)
                    for fleet_chunk in self._chunk_list(fleet_ids_list, 50):
                        fleet_response = self._retry_with_backoff(
                            self.aws_client.ec2_client.describe_fleets,
                            operation_type="read_only",
                            FleetIds=fleet_chunk,
                        )

                        for fleet_details in fleet_response.get("Fleets", []):
                            fleet_id = fleet_details.get("FleetId")
                            if fleet_id in fleet_groups:
                                fleet_groups[fleet_id]["fleet_details"] = fleet_details

                except Exception as e:
                    self._logger.warning(f"Failed to fetch EC2 Fleet details: {e}")
                    # Continue without fleet details - methods will handle missing details

        except Exception as e:
            self._logger.error(f"Failed to group instances by EC2 Fleet: {e}")
            # Fallback: treat all instances as non-EC2 Fleet
            fleet_groups = {None: {"instance_ids": instance_ids.copy()}}

        self._logger.debug(f"Grouped {len(instance_ids)} instances into {len(fleet_groups)} groups")
        return fleet_groups

    def _find_ec2_fleet_for_instance(self, instance_id: str) -> Optional[str]:
        """
        Find the EC2 Fleet ID for a specific instance by querying active fleets.

        Args:
            instance_id: EC2 instance ID

        Returns:
            EC2 Fleet ID if found, None otherwise
        """
        try:
            # Get all active EC2 fleets
            response = self._retry_with_backoff(
                lambda: self._paginate(
                    self.aws_client.ec2_client.describe_fleets,
                    "Fleets",
                    FleetStates=["active", "modifying"],
                ),
                operation_type="read_only",
            )

            # Check each fleet for the instance
            for fleet in response:
                fleet_id = fleet.get("FleetId")
                if not fleet_id:
                    continue

                try:
                    # Get instances for this fleet
                    fleet_instances = self._retry_with_backoff(
                        lambda fid=fleet_id: self._collect_with_next_token(
                            self.aws_client.ec2_client.describe_fleet_instances,
                            "ActiveInstances",
                            FleetId=fid,
                        )
                    )

                    # Check if our instance is in this fleet
                    for instance in fleet_instances:
                        if instance.get("InstanceId") == instance_id:
                            return fleet_id

                except Exception as e:
                    self._logger.debug(
                        f"Failed to check fleet {fleet_id} for instance {instance_id}: {e}"
                    )
                    continue

        except Exception as e:
            self._logger.debug(f"Failed to find EC2 Fleet for instance {instance_id}: {e}")

        return None

    def _release_hosts_for_single_ec2_fleet(
        self, fleet_id: str, fleet_instance_ids: list[str], fleet_details: dict
    ) -> None:
        """Release hosts for a single EC2 Fleet with proper fleet management."""
        self._logger.info(
            f"Processing EC2 Fleet {fleet_id} with {len(fleet_instance_ids)} instances"
        )

        try:
            # Get fleet configuration with pagination and retry
            if not fleet_details:
                fleet_list = self._retry_with_backoff(
                    lambda: self._paginate(
                        self.aws_client.ec2_client.describe_fleets,
                        "Fleets",
                        FleetIds=[fleet_id],
                    ),
                    operation_type="read_only",
                )

                if not fleet_list:
                    self._logger.warning(
                        f"EC2 Fleet {fleet_id} not found, terminating instances directly"
                    )
                    self.aws_ops.terminate_instances_with_fallback(
                        fleet_instance_ids, self._request_adapter, f"EC2Fleet-{fleet_id} instances"
                    )
                    return

                fleet_details = fleet_list[0]

            fleet_type = fleet_details.get("Type", "maintain")

            if fleet_instance_ids:
                if fleet_type == "maintain":
                    # For maintain fleets, reduce target capacity first to prevent replacements
                    current_capacity = fleet_details["TargetCapacitySpecification"][
                        "TotalTargetCapacity"
                    ]
                    new_capacity = max(0, current_capacity - len(fleet_instance_ids))

                    self._logger.info(
                        "Reducing maintain fleet %s capacity from %s to %s before terminating instances",
                        fleet_id,
                        current_capacity,
                        new_capacity,
                    )

                    self._retry_with_backoff(
                        self.aws_client.ec2_client.modify_fleet,
                        operation_type="critical",
                        FleetId=fleet_id,
                        TargetCapacitySpecification={"TotalTargetCapacity": new_capacity},
                    )

                # Terminate specific instances using existing utility
                self.aws_ops.terminate_instances_with_fallback(
                    fleet_instance_ids, self._request_adapter, f"EC2Fleet-{fleet_id} instances"
                )
                self._logger.info(
                    "Terminated EC2 Fleet %s instances: %s", fleet_id, fleet_instance_ids
                )

                # If capacity has reached zero for maintain fleets, delete the fleet
                if fleet_type == "maintain" and new_capacity == 0:
                    self._logger.info("EC2 Fleet %s capacity is zero, deleting fleet", fleet_id)
                    self._delete_ec2_fleet(fleet_id)
            else:
                # If no specific instances provided, delete entire fleet
                self._delete_ec2_fleet(fleet_id)

        except Exception as e:
            self._logger.error("Failed to terminate EC2 fleet %s: %s", fleet_id, e)
            raise

    def _delete_ec2_fleet(self, fleet_id: str) -> None:
        """Delete an EC2 Fleet when it's no longer needed."""
        try:
            self._logger.info(f"Deleting EC2 Fleet {fleet_id}")

            # Delete the fleet with termination of instances
            self._retry_with_backoff(
                self.aws_client.ec2_client.delete_fleets,
                operation_type="critical",
                FleetIds=[fleet_id],
                TerminateInstances=True,
            )

            self._logger.info(f"Successfully deleted EC2 Fleet {fleet_id}")

        except Exception as e:
            # Log the error but don't fail the entire operation
            # Fleet deletion is cleanup - the main termination should still succeed
            self._logger.warning(f"Failed to delete EC2 Fleet {fleet_id}: {e}")
            self._logger.warning(
                "EC2 Fleet deletion failed, but instance termination completed successfully"
            )

    @staticmethod
    def _chunk_list(items: list[str], chunk_size: int):
        """Yield successive chunk-sized lists from items."""
        for index in range(0, len(items), chunk_size):
            yield items[index : index + chunk_size]
