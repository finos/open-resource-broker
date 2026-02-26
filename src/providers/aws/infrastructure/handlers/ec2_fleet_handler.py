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
from typing import Any, Optional

from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from domain.template.template_aggregate import Template
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.resilience import CircuitBreakerOpenError
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
from providers.aws.infrastructure.handlers.fleet_grouping_mixin import FleetGroupingMixin
from providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from providers.aws.utilities.aws_operations import AWSOperations


@injectable
class EC2FleetHandler(AWSHandler, BaseContextMixin, FleetGroupingMixin):
    """Handler for EC2 Fleet operations."""

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: RequestAdapterPort = None,
        machine_adapter: Optional[AWSMachineAdapter] = None,
        aws_native_spec_service=None,
        config_port=None,
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
            aws_native_spec_service=aws_native_spec_service,
            config_port=config_port,
        )

    @handle_infrastructure_exceptions(context="ec2_fleet_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> dict[str, Any]:
        """
        Create an EC2 Fleet to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            fleet_result = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_fleet_internal(request, aws_template),
                operation_name="create EC2 fleet",
                context="EC2Fleet",
            )
            fleet_id = fleet_result["fleet_id"]

            # Get instance details based on fleet type
            instances: list[dict[str, Any]] = []
            instance_details: list[dict[str, Any]] = []
            fleet_type = aws_template.fleet_type
            if not isinstance(fleet_type, AWSFleetType):
                try:
                    fleet_type = AWSFleetType(str(fleet_type))
                except Exception:
                    fleet_type = None

            if fleet_type is AWSFleetType.INSTANT:
                # For instant fleets, instance IDs are in the result dict
                instance_ids = fleet_result.get("instance_ids", [])
                if instance_ids:
                    instances = self._get_instance_details(
                        instance_ids,
                        request_id=str(request.request_id),
                        resource_id=fleet_id,
                        provider_api="EC2Fleet",
                    )

                    # Collect detailed instance information for enhanced monitoring
                    for instance in instances:
                        instance_detail = {
                            "instance_id": instance.get("instance_id"),
                            "instance_type": instance.get("instance_type"),
                            "availability_zone": instance.get("availability_zone"),
                            "launch_time": instance.get("launch_time"),
                            "state": instance.get("state"),
                            "private_ip": instance.get("private_ip_address"),
                            "public_ip": instance.get("public_ip_address"),
                            "fleet_id": fleet_id,
                            "fleet_type": aws_template.fleet_type,
                        }
                        instance_details.append(instance_detail)

                    # Log detailed instance information for monitoring
                    self._logger.info(
                        "EC2Fleet instance details collected",
                        extra={
                            "fleet_id": fleet_id,
                            "instance_count": len(instance_details),
                            "instance_details": instance_details,
                        },
                    )

            return {
                "success": True,
                "resource_ids": [fleet_id],
                "instances": instances,
                "provider_data": {
                    "resource_type": "ec2_fleet",
                    "fleet_type": aws_template.fleet_type.value
                    if hasattr(aws_template.fleet_type, "value")
                    else aws_template.fleet_type,
                    "fleet_errors": fleet_result.get("metadata_updates", {}).get(
                        "fleet_errors", []
                    ),
                },
            }
        except Exception as e:
            return {
                "success": False,
                "resource_ids": [],
                "instances": [],
                "error_message": str(e),
            }

    def _create_fleet_internal(self, request: Request, aws_template: AWSTemplate) -> dict[str, Any]:
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

        fleet_type = aws_template.fleet_type
        if fleet_type.value not in valid_types:
            raise AWSValidationError(
                f"Invalid EC2 fleet type: {aws_template.fleet_type}. "
                f"Must be one of: {', '.join(valid_types)}"
            )

        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
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

        instance_ids = self._extract_instant_instance_ids(response)

        # Check for errors in response (especially for instant fleets)
        errors = self._extract_fleet_errors(response)
        if errors:
            error_summary = "; ".join(
                f"{error.get('error_code', 'Unknown')}: {error.get('error_message', 'No message')}"
                for error in errors
            )
            error_context = self._record_fleet_error_details(
                request=request,
                fleet_id=fleet_id,
                errors=errors,
                response=response,
                instance_ids=instance_ids,
            )
            instance_ids = error_context.get("metadata_updates", {}).get(
                "instance_ids", instance_ids
            )

            if not instance_ids:
                self._logger.error(
                    "EC2 Fleet %s returned %d error(s) during creation: %s",
                    fleet_id,
                    len(errors),
                    error_summary,
                )
                raise AWSInfrastructureError(
                    f"Fleet {fleet_id} creation failed with {len(errors)} error(s): {error_summary}"
                )
            self._logger.warning(
                "EC2 Fleet %s returned errors (%d) but also created %d instance(s); treating as partial success. Errors: %s",
                fleet_id,
                len(errors),
                len(instance_ids),
                error_summary,
            )

        # For instant fleets, log instance IDs
        if fleet_type == AWSFleetType.INSTANT:
            if instance_ids:
                self._logger.debug("Stored instance IDs in request metadata: %s", instance_ids)
            else:
                self._logger.warning(
                    "No instance IDs found in instant fleet response (no errors reported). Response: %s",
                    response,
                )

        return {"fleet_id": fleet_id, "instance_ids": instance_ids}

    def _extract_instant_instance_ids(self, response: dict[str, Any]) -> list[str]:
        """Extract instance IDs from an instant fleet response."""
        instance_ids: list[str] = []
        for inst_block in response.get("Instances", []):
            for instance_id in inst_block.get("InstanceIds", []):
                instance_ids.append(instance_id)
        return instance_ids

    def _extract_fleet_errors(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Normalize EC2 Fleet error payloads for logging and persistence."""
        errors = response.get("Errors") or []
        if isinstance(errors, dict):
            errors = [errors]
        if not isinstance(errors, list):
            return [{"error_code": "Unknown", "error_message": str(errors)}]

        normalized: list[dict[str, Any]] = []
        for error in errors:
            if not isinstance(error, dict):
                normalized.append(
                    {"error_code": "Unknown", "error_message": str(error), "lifecycle": None}
                )
                continue

            lt_overrides = error.get("LaunchTemplateAndOverrides", {}) or {}
            lt_spec = lt_overrides.get("LaunchTemplateSpecification", {}) or {}
            overrides = lt_overrides.get("Overrides", {}) or {}

            normalized.append(
                {
                    "error_code": error.get("ErrorCode", "Unknown"),
                    "error_message": error.get("ErrorMessage", "No message"),
                    "lifecycle": error.get("Lifecycle"),
                    "launch_template_id": lt_spec.get("LaunchTemplateId"),
                    "launch_template_version": lt_spec.get("Version"),
                    "subnet_id": overrides.get("SubnetId"),
                    "instance_type": overrides.get("InstanceType"),
                    "instance_requirements": overrides.get("InstanceRequirements"),
                }
            )

        return normalized

    def _record_fleet_error_details(
        self,
        request: Request,
        fleet_id: str,
        errors: list[dict[str, Any]],
        response: dict[str, Any],
        instance_ids: list[str],
    ) -> dict[str, Any]:
        """Return fleet error context for downstream status handling."""
        response_metadata = response.get("ResponseMetadata")
        metadata_updates: dict[str, Any] = {
            "fleet_id": fleet_id,
            "fleet_errors": errors,
        }
        if response_metadata:
            metadata_updates["fleet_response_metadata"] = response_metadata
        if instance_ids:
            metadata_updates["instance_ids"] = instance_ids
        return {"metadata_updates": metadata_updates}

    def _format_instance_data(
        self,
        instance_details: list[dict[str, Any]],
        resource_id: str,
        request: Request,
        aws_template: Optional[AWSTemplate] = None,
    ) -> list[dict[str, Any]]:
        """Format AWS instance details to standard structure.

        instance_details is already snake_case from _get_instance_details.
        Just stamp handler-specific context fields.
        """
        if aws_template and aws_template.provider_api is not None:
            provider_api_value = (
                aws_template.provider_api.value
                if hasattr(aws_template.provider_api, "value")
                else str(aws_template.provider_api)
            )
        else:
            metadata = getattr(request, "metadata", {}) or {}
            provider_api_value = metadata.get("provider_api", "EC2Fleet")

        result = []
        for inst in instance_details:
            stamped = dict(inst)
            stamped.setdefault("resource_id", resource_id)
            stamped.setdefault("provider_api", provider_api_value)
            result.append(stamped)
        return result

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
        if template.machine_types and template.subnet_ids:
            for subnet_id in template.subnet_ids:
                for instance_type, weight in template.machine_types.items():
                    instance_overrides.append(
                        {
                            "instance_type": instance_type,
                            "subnet_id": subnet_id,
                            "weighted_capacity": weight,
                        }
                    )
        elif template.machine_types:
            for instance_type, weight in template.machine_types.items():
                instance_overrides.append(
                    {"instance_type": instance_type, "weighted_capacity": weight}
                )

        # On-demand instance overrides for heterogeneous fleets
        ondemand_overrides = []
        if (
            template.price_type == "heterogeneous"
            and hasattr(template, "machine_types_ondemand")
            and template.machine_types_ondemand
        ):
            for instance_type, weight in template.machine_types_ondemand.items():
                ondemand_overrides.append(
                    {"instance_type": instance_type, "weighted_capacity": weight}
                )

        # Capacity split for heterogeneous pricing
        percent_on_demand = template.percent_on_demand or 0
        on_demand_count = int(request.requested_count * percent_on_demand / 100)
        spot_count = request.requested_count - on_demand_count

        # ABIS instance requirements
        abis_instance_requirements = template.get_instance_requirements_payload()
        has_abis = bool(abis_instance_requirements)

        # When ABIS is set, add per-subnet InstanceRequirements overrides
        if has_abis and not instance_overrides:
            if template.subnet_ids:
                for subnet_id in template.subnet_ids:
                    instance_overrides.append(
                        {
                            "instance_type": None,
                            "subnet_id": subnet_id,
                            "weighted_capacity": None,
                            "instance_requirements": abis_instance_requirements,
                        }
                    )
            else:
                instance_overrides.append(
                    {
                        "instance_type": None,
                        "subnet_id": None,
                        "weighted_capacity": None,
                        "instance_requirements": abis_instance_requirements,
                    }
                )

        return {
            # Fleet-specific values
            "fleet_type": template.fleet_type.value,
            "fleet_name": f"{self.config_port.get_resource_prefix('fleet')}{request.request_id}",
            # Computed overrides
            "instance_overrides": instance_overrides,
            "ondemand_overrides": ondemand_overrides,
            "needs_overrides": bool(instance_overrides or ondemand_overrides),
            # Fleet-specific flags
            "is_maintain_fleet": template.fleet_type == AWSFleetType.MAINTAIN,
            "is_instant_fleet": template.fleet_type == AWSFleetType.INSTANT,
            "replace_unhealthy": template.fleet_type == AWSFleetType.MAINTAIN,
            "has_spot_options": bool(template.allocation_strategy or template.max_price),
            "has_ondemand_options": bool(template.allocation_strategy_on_demand),
            "is_heterogeneous": template.price_type == "heterogeneous",
            # ABIS / InstanceRequirements
            "abis_instance_requirements": abis_instance_requirements,
            "has_abis": has_abis,
            # Capacity split
            "percent_on_demand": percent_on_demand,
            "on_demand_count": on_demand_count,
            "spot_count": spot_count,
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
                    if template.abis_instance_requirements:
                        overrides = native_spec["LaunchTemplateConfigs"][0].get("Overrides", [])
                        if not any("InstanceRequirements" in o for o in overrides):
                            native_spec["LaunchTemplateConfigs"][0]["Overrides"] = [
                                {"InstanceRequirements": template.get_instance_requirements_payload()}
                            ]
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
            "TagSpecifications": [],
        }

        fleet_tags = [
            {
                "Key": "Name",
                "Value": f"{self.config_port.get_resource_prefix('fleet')}{request.request_id}",
            },
            {"Key": "RequestId", "Value": str(request.request_id)},
            {"Key": "TemplateId", "Value": str(template.template_id)},
            {"Key": "CreatedBy", "Value": created_by},
            {"Key": "CreatedAt", "Value": datetime.utcnow().isoformat()},
            {"Key": "ProviderApi", "Value": "EC2Fleet"},
        ]
        if template.tags:
            fleet_tags.extend([{"Key": k, "Value": v} for k, v in template.tags.items()])
        fleet_config["TagSpecifications"] = [
            {"ResourceType": "fleet", "Tags": fleet_tags},
        ]
        if template.fleet_type == AWSFleetType.INSTANT:
            fleet_config["TagSpecifications"].append(
                {"ResourceType": "instance", "Tags": fleet_tags}
            )

        # Add fleet type specific configurations
        if template.fleet_type == AWSFleetType.MAINTAIN:
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

        instance_requirements_payload = template.get_instance_requirements_payload()

        if instance_requirements_payload:
            overrides = []
            if template.subnet_ids:
                for subnet_id in template.subnet_ids:
                    override = {
                        "SubnetId": subnet_id,
                        "InstanceRequirements": instance_requirements_payload,
                    }
                    overrides.append(override)
            else:
                overrides.append({"InstanceRequirements": instance_requirements_payload})

            fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides
        else:
            from providers.aws.infrastructure.handlers.fleet_override_builder import (
                build_ec2_fleet_overrides,
            )

            overrides = build_ec2_fleet_overrides(
                template.machine_types,
                template.machine_types_ondemand,
                template.subnet_ids,
                price_type == "heterogeneous",
            )
            if overrides:
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides

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

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Check the status of instances in the fleet."""
        self._logger.debug(f" check_hosts_status {request}")
        if not request.resource_ids:
            raise AWSInfrastructureError("No Fleet ID found in request")

        all_results: list[dict] = []
        for fleet_id in request.resource_ids:
            try:
                results = self._check_single_fleet_status(fleet_id, request)
                all_results.extend(results)
            except Exception as e:
                self._logger.warning(
                    "Failed to check status for fleet %s, skipping: %s", fleet_id, e
                )
        return all_results

    def _check_single_fleet_status(self, fleet_id: str, request: Request) -> list[dict]:
        """Check the status of instances in a single fleet."""
        try:
            fleet_type_value = request.metadata.get("fleet_type")

            fleet_type = None
            if fleet_type_value:
                try:
                    fleet_type = AWSFleetType(fleet_type_value.lower())
                except Exception:
                    self._logger.warning(
                        "Invalid fleet_type '%s' in metadata for request %s; will derive from AWS response",
                        fleet_type_value,
                        request.request_id,
                    )

            fleet_list = self._retry_with_backoff(
                lambda: self._paginate(
                    self.aws_client.ec2_client.describe_fleets,
                    "Fleets",
                    FleetIds=[fleet_id],
                ),
                operation_type="read_only",
            )

            self._logger.debug(
                f" check_hosts_status fleet_type [{fleet_type}] [type: {type(fleet_list[0]) if fleet_list else None}]fleet_list: {fleet_list}"
            )

            if not fleet_list:
                raise AWSEntityNotFoundError(f"Fleet {fleet_id} not found")

            fleet = fleet_list[0]

            if fleet_type is None:
                derived_type = fleet.get("Type") or fleet.get("FleetType") or "maintain"
                fleet_type = AWSFleetType(str(derived_type).lower())
                self._logger.debug(
                    "Derived fleet_type '%s' from DescribeFleets response for fleet %s",
                    fleet_type,
                    fleet_id,
                )

            self._logger.debug(f" check_hosts_status final fleet_type: {fleet_type}")

            self._logger.debug(
                "Fleet status: %s, Target capacity: %s, Fulfilled capacity: %s",
                fleet.get("FleetState"),
                fleet.get("TargetCapacitySpecification", {}).get("TotalTargetCapacity"),
                fleet.get("FulfilledCapacity", 0),
            )

            instance_ids = []
            if fleet_type == AWSFleetType.INSTANT:
                metadata_instance_ids = request.metadata.get("instance_ids", [])
                if metadata_instance_ids:
                    instance_ids = metadata_instance_ids
                    self._logger.debug(
                        "Instant fleet %s using instance_ids from metadata: %s",
                        fleet_id,
                        instance_ids,
                    )
                else:
                    instance_ids = [
                        instance_id
                        for instance in fleet.get("Instances", [])
                        for instance_id in instance.get("InstanceIds", [])
                    ]
                    self._logger.debug(
                        "Instant fleet %s derived instance_ids from DescribeFleets response: %s",
                        fleet_id,
                        instance_ids,
                    )
            else:
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

            instance_details = self._get_instance_details(
                instance_ids,
                request_id=str(request.request_id),
                resource_id=fleet_id,
                provider_api="EC2Fleet",
            )
            return self._format_instance_data(instance_details, fleet_id, request)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to check EC2 Fleet status: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error checking EC2 Fleet status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check EC2 Fleet status: {e!s}")

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
    ) -> None:
        """Release hosts across multiple EC2 Fleets by detecting fleet membership.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: Dict mapping instance_id to (resource_id or None, desired_capacity)
        """
        try:
            if not machine_ids:
                self._logger.warning("No instance IDs provided for EC2 Fleet termination")
                return

            self._logger.info("Releasing hosts for %d instances: %s", len(machine_ids), machine_ids)

            # Use resource_mapping if available, otherwise fall back to AWS API calls
            if resource_mapping:
                filtered_mapping = {
                    instance_id: resource_mapping.get(instance_id, (None, 0))
                    for instance_id in machine_ids
                }
                fleet_instance_groups = self._group_instances_by_ec2_fleet_from_mapping(
                    machine_ids, filtered_mapping
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
            fleet_errors: list[tuple[str, str]] = []
            for fleet_id, fleet_data in fleet_instance_groups.items():
                if fleet_id is not None:
                    # Handle EC2 Fleet instances using dedicated method (primary case)
                    try:
                        self._release_hosts_for_single_ec2_fleet(
                            fleet_id, fleet_data["instance_ids"], fleet_data["fleet_details"]
                        )
                    except AWSInfrastructureError:
                        raise
                    except Exception as e:
                        fleet_errors.append((fleet_id, str(e)))
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

            if fleet_errors:
                raise AWSInfrastructureError(
                    f"Failed to release {len(fleet_errors)} fleet(s): {fleet_errors}"
                )

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to release EC2 Fleet resources: %s", str(error))
            raise error
        except AWSInfrastructureError:
            raise
        except Exception as e:
            self._logger.error("Failed to release EC2 Fleet hosts: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release EC2 Fleet hosts: {e!s}")

    def _group_instances_by_ec2_fleet_from_mapping(
        self, machine_ids: list[str], resource_mapping: dict[str, tuple[Optional[str], int]]
    ) -> dict[Optional[str], dict]:
        """Group EC2 Fleet instances using shared mixin logic."""
        return self._group_instances_from_mapping(machine_ids, resource_mapping)

    def _group_instances_by_ec2_fleet(self, instance_ids: list[str]) -> dict[Optional[str], dict]:
        """Group EC2 Fleet instances via AWS lookups only."""
        return self._group_instances_direct(instance_ids)

    # FleetGroupingMixin hooks
    def _collect_groups_from_instances(
        self,
        instance_ids: list[str],
        groups: dict[Optional[str], dict],
        group_ids_to_fetch: set[str],
    ) -> None:
        """Populate EC2 Fleet groups using describe_instances lookups."""
        if not instance_ids:
            return

        try:
            for chunk in self._chunk_list(instance_ids, self.grouping_chunk_size):
                try:
                    response = self._retry_with_backoff(
                        self.aws_client.ec2_client.describe_instances,
                        operation_type="read_only",
                        InstanceIds=chunk,
                    )

                    ec2_fleet_instance_ids = set()

                    for reservation in response.get("Reservations", []):
                        for instance in reservation.get("Instances", []):
                            instance_id = instance.get("InstanceId")
                            if not instance_id:
                                continue

                            ec2_fleet_id = None
                            for tag in instance.get("Tags", []):
                                if tag.get("Key") == "aws:ec2:fleet-id":
                                    ec2_fleet_id = tag.get("Value")
                                    break

                            if not ec2_fleet_id:
                                ec2_fleet_id = self._find_ec2_fleet_for_instance(instance_id)

                            if ec2_fleet_id:
                                self._add_instance_to_group(groups, ec2_fleet_id, instance_id)
                                ec2_fleet_instance_ids.add(instance_id)
                                group_ids_to_fetch.add(ec2_fleet_id)

                    non_ec2_fleet_instances = [
                        iid for iid in chunk if iid not in ec2_fleet_instance_ids
                    ]
                    for iid in non_ec2_fleet_instances:
                        self._add_non_group_instance(groups, iid)

                except Exception as exc:
                    self._logger.warning(
                        "Failed to describe EC2 Fleet instances for chunk %s: %s", chunk, exc
                    )
                    for iid in chunk:
                        self._add_non_group_instance(groups, iid)

        except Exception as exc:
            self._logger.error("Failed to group instances by EC2 Fleet: %s", exc)
            groups.clear()
            group_ids_to_fetch.clear()
            groups[None] = {"instance_ids": instance_ids.copy()}

    def _fetch_and_attach_group_details(
        self, group_ids: set[str], groups: dict[Optional[str], dict]
    ) -> None:
        """Fetch EC2 Fleet details for grouped fleets."""
        if not group_ids:
            return

        try:
            fleet_ids_list = list(group_ids)
            for fleet_chunk in self._chunk_list(fleet_ids_list, self.grouping_chunk_size):
                fleet_response = self._retry_with_backoff(
                    self.aws_client.ec2_client.describe_fleets,
                    operation_type="read_only",
                    FleetIds=fleet_chunk,
                )

                for fleet_details in fleet_response.get("Fleets", []):
                    fleet_id = fleet_details.get("FleetId")
                    if fleet_id in groups:
                        groups[fleet_id]["fleet_details"] = fleet_details

        except Exception as exc:
            self._logger.warning("Failed to fetch EC2 Fleet details: %s", exc)

    def _grouping_label(self) -> str:
        return "EC2 Fleet"

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

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Get example templates for EC2Fleet handler covering all fleet type x price type combinations."""
        return [
            # Instant fleet types
            AWSTemplate(
                template_id="EC2Fleet-Instant-OnDemand",
                name="EC2 Fleet Instant On-Demand",
                description="EC2 Fleet with instant fulfillment using on-demand instances",
                provider_api="EC2Fleet",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=10,
                price_type="ondemand",
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "dev", "ManagedBy": "ORB"},
                metadata={"fleet_type": "instant"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Instant-Spot",
                name="EC2 Fleet Instant Spot",
                description="EC2 Fleet with instant fulfillment using spot instances",
                provider_api="EC2Fleet",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=10,
                price_type="spot",
                max_price=0.05,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "dev", "ManagedBy": "ORB"},
                metadata={"fleet_type": "instant"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Instant-Mixed",
                name="EC2 Fleet Instant Mixed",
                description="EC2 Fleet with instant fulfillment using mixed pricing",
                provider_api="EC2Fleet",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=10,
                price_type="heterogeneous",
                percent_on_demand=30,
                allocation_strategy="diversified",
                max_price=0.05,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "dev", "ManagedBy": "ORB"},
                metadata={"fleet_type": "instant", "percent_on_demand": 30},
            ),
            # Request fleet types
            AWSTemplate(
                template_id="EC2Fleet-Request-OnDemand",
                name="EC2 Fleet Request On-Demand",
                description="EC2 Fleet with request fulfillment using on-demand instances",
                provider_api="EC2Fleet",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=15,
                price_type="ondemand",
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "test", "ManagedBy": "ORB"},
                metadata={"fleet_type": "request"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Request-Spot",
                name="EC2 Fleet Request Spot",
                description="EC2 Fleet with request fulfillment using spot instances",
                provider_api="EC2Fleet",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=20,
                price_type="spot",
                allocation_strategy="capacityOptimized",
                max_price=0.08,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "test", "ManagedBy": "ORB"},
                metadata={"fleet_type": "request"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Request-Mixed",
                name="EC2 Fleet Request Mixed",
                description="EC2 Fleet with request fulfillment using mixed pricing",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                image_id="ami-12345678",
                max_instances=25,
                price_type="heterogeneous",
                percent_on_demand=40,
                allocation_strategy="diversified",
                allocation_strategy_on_demand="lowestPrice",
                max_price=0.08,
                subnet_ids=["subnet-12345678", "subnet-87654321"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "test", "ManagedBy": "ORB"},
                metadata={"fleet_type": "request", "percent_on_demand": 40},
            ),
            # Maintain fleet types
            AWSTemplate(
                template_id="EC2Fleet-Maintain-OnDemand",
                name="EC2 Fleet Maintain On-Demand",
                description="EC2 Fleet with maintain capacity using on-demand instances",
                provider_api="EC2Fleet",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=12,
                price_type="ondemand",
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "prod", "ManagedBy": "ORB"},
                metadata={"fleet_type": "maintain"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Maintain-Spot",
                name="EC2 Fleet Maintain Spot",
                description="EC2 Fleet with maintain capacity using spot instances",
                provider_api="EC2Fleet",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=30,
                price_type="spot",
                allocation_strategy="priceCapacityOptimized",
                max_price=0.10,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "prod", "ManagedBy": "ORB"},
                metadata={"fleet_type": "maintain"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Maintain-Mixed",
                name="EC2 Fleet Maintain Mixed",
                description="EC2 Fleet with maintain capacity using mixed pricing",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1, "t3.large": 2, "t3.xlarge": 3},
                image_id="ami-12345678",
                max_instances=50,
                price_type="heterogeneous",
                percent_on_demand=50,
                allocation_strategy="capacityOptimized",
                allocation_strategy_on_demand="prioritized",
                max_price=0.12,
                subnet_ids=["subnet-12345678", "subnet-87654321", "subnet-11223344"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "prod", "ManagedBy": "ORB"},
                metadata={"fleet_type": "maintain", "percent_on_demand": 50},
            ),
        ]
