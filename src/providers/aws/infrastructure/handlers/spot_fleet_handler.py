"""AWS Spot Fleet Handler.

This module provides the Spot Fleet handler implementation for managing
AWS Spot Fleet requests through the AWS EC2 Spot Fleet API.

The Spot Fleet handler enables cost-effective provisioning of EC2 instances
using Spot pricing with automatic diversification across instance types
and availability zones to maximize availability and minimize costs.

Key Features:
    - Spot instance cost optimization
    - Multiple instance type support
    - Automatic diversification strategies
    - Fault tolerance across AZs
    - Flexible capacity management

Classes:
    SpotFleetHandler: Main handler for Spot Fleet operations

Usage:
    This handler is used by the AWS provider to manage Spot Fleet requests
    for cost-sensitive workloads that can tolerate interruptions.

Note:
    Spot Fleet is ideal for batch processing, CI/CD, and other workloads
    that can benefit from significant cost savings through Spot pricing.
"""

import json
from typing import Any, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from domain.template.template_aggregate import Template
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from providers.aws.infrastructure.tags import build_system_tags, merge_tags
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.domain.template.value_objects import AWSFleetType
from providers.aws.exceptions.aws_exceptions import (
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
class SpotFleetHandler(AWSHandler, BaseContextMixin, FleetGroupingMixin):
    """Handler for Spot Fleet operations."""

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: RequestAdapterPort = None,  # type: ignore[assignment]
        machine_adapter: Optional[AWSMachineAdapter] = None,
        aws_native_spec_service=None,
        config_port=None,
    ) -> None:
        """
        Initialize the Spot Fleet handler.

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

    @handle_infrastructure_exceptions(context="spot_fleet_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> dict[str, Any]:
        """
        Create a Spot Fleet to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            response = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_spot_fleet_with_response(request, aws_template),
                operation_name="create Spot Fleet",
                context="SpotFleet",
            )

            fleet_id = response["SpotFleetRequestId"]

            instances = []

            return {
                "success": True,
                "resource_ids": [fleet_id],
                "instance_ids": [],  # SpotFleet doesn't return instance IDs immediately
                "instances": instances,
                "provider_data": {"resource_type": "spot_fleet"},
            }
        except Exception as e:
            self._logger.error("SpotFleet creation failed: %s", e)
            return {"success": False, "resource_ids": [], "instances": [], "error_message": str(e)}

    def _create_spot_fleet_with_response(
        self, request: Request, aws_template: AWSTemplate
    ) -> dict[str, Any]:
        """Create Spot Fleet and return full AWS response."""
        # Validate Spot Fleet specific prerequisites
        self._validate_spot_prerequisites(aws_template)

        # Validate fleet type
        if not aws_template.fleet_type:
            raise AWSValidationError("Fleet type is required for SpotFleet")

        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
        )

        # Store launch template info in request (if request has this method)
        if hasattr(request, "set_launch_template_info"):
            request.set_launch_template_info(  # type: ignore[attr-defined]
                launch_template_result.template_id, launch_template_result.version
            )

        # Create spot fleet configuration
        fleet_config = self._create_spot_fleet_config(
            template=aws_template,
            request=request,
            launch_template_id=launch_template_result.template_id,
            launch_template_version=launch_template_result.version,
        )

        # Request spot fleet with circuit breaker for critical operation
        response = self._retry_with_backoff(
            self.aws_client.ec2_client.request_spot_fleet,
            operation_type="critical",
            SpotFleetRequestConfig=fleet_config,
        )

        fleet_id = response["SpotFleetRequestId"]
        self._logger.info("Successfully created Spot Fleet request: %s", fleet_id)

        return response

    def _validate_spot_prerequisites(self, aws_template: AWSTemplate) -> None:
        """Validate Spot Fleet specific prerequisites."""
        errors = []

        # Log the validation start
        self._logger.debug(
            "Starting Spot Fleet prerequisites validation for template: %s",
            aws_template.template_id,
        )

        # First validate common prerequisites
        try:
            self._validate_prerequisites(aws_template)
        except AWSValidationError as e:
            errors.extend(str(e).split("\n"))

        # Validate Spot Fleet specific requirements
        if not hasattr(aws_template, "fleet_role") or not aws_template.fleet_role:
            errors.append("Fleet role ARN is required for Spot Fleet")
        # For service-linked roles, we only validate the format
        elif "AWSServiceRoleForEC2SpotFleet" in aws_template.fleet_role:
            if not self._is_valid_spot_fleet_service_role(aws_template.fleet_role):
                errors.append(
                    f"Invalid Spot Fleet service-linked role format: {aws_template.fleet_role}. "
                    f"Expected full ARN: arn:aws:iam::<account_id>:role/aws-service-role/"
                    f"spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
                )
        elif self._is_valid_spot_fleet_tagging_role(aws_template.fleet_role):
            # Well-known tagging role ARN — format already validated, no IAM call needed
            self._logger.debug("Valid Spot Fleet tagging role: %s", aws_template.fleet_role)
        else:
            # For custom roles, validate with IAM
            try:
                role_name = aws_template.fleet_role.split("/")[-1]
                # Create IAM client directly from session
                iam_client = self.aws_client.session.client(
                    "iam", config=self.aws_client.boto_config
                )
                self._retry_with_backoff(iam_client.get_role, RoleName=role_name)
            except Exception as e:
                errors.append(f"Invalid custom fleet role: {e!s}")

        # Validate price type if specified
        if hasattr(aws_template, "price_type") and aws_template.price_type:
            valid_options = ["spot", "ondemand", "heterogeneous"]
            if aws_template.price_type not in valid_options:
                errors.append(
                    f"Invalid price type: {aws_template.price_type}. "
                    f"Must be one of: {', '.join(valid_options)}"
                )

        # For heterogeneous price type, validate percent_on_demand
        if (
            hasattr(aws_template, "price_type")
            and aws_template.price_type == "heterogeneous"
            and (
                not hasattr(aws_template, "percent_on_demand")
                or aws_template.percent_on_demand is None
            )
        ):
            errors.append("percent_on_demand is required for heterogeneous price type")

        # For heterogeneous price type with machine_types_ondemand, validate the
        # configuration
        if (
            hasattr(aws_template, "price_type")
            and aws_template.price_type == "heterogeneous"
            and hasattr(aws_template, "machine_types_ondemand")
            and aws_template.machine_types_ondemand
        ):
            # Validate that machine_types is also specified
            if not hasattr(aws_template, "machine_types") or not aws_template.machine_types:
                errors.append("machine_types must be specified when using machine_types_ondemand")

            # Validate that machine_types_ondemand has valid instance types
            for instance_type, weight in aws_template.machine_types_ondemand.items():
                if not isinstance(weight, int) or weight <= 0:
                    errors.append(
                        f"Weight for on-demand instance type {instance_type} must be a positive integer"
                    )

        # Validate spot price if specified
        if hasattr(aws_template, "max_price") and aws_template.max_price is not None:
            try:
                price = float(aws_template.max_price)
                if price <= 0:
                    errors.append("Spot price must be greater than zero")
            except ValueError:
                errors.append("Invalid spot price format")

        if errors:
            self._logger.error("Validation errors found: %s", errors)
            raise AWSValidationError("\n".join(errors))
        else:
            self._logger.debug("All Spot Fleet prerequisites validation passed")

    def _is_valid_spot_fleet_service_role(self, role_arn: str) -> bool:
        """
        Validate if the provided ARN matches the Spot Fleet service-linked role pattern.

        Args:
            role_arn: The role ARN to validate

        Returns:
            bool: True if the ARN matches the expected pattern
        """
        import re

        pattern = (
            r"^arn:aws:iam::\d{12}:role/aws-service-role/"
            r"spotfleet\.amazonaws\.com/AWSServiceRoleForEC2SpotFleet$"
        )

        if re.match(pattern, role_arn):
            self._logger.debug("Valid Spot Fleet service-linked role: %s", role_arn)
            return True
        return False

    def _is_valid_spot_fleet_tagging_role(self, role_arn: str) -> bool:
        """Validate if the provided ARN matches the EC2 Spot Fleet tagging role pattern."""
        import re

        pattern = r"^arn:aws:iam::\d{12}:role/aws-ec2-spot-fleet-tagging-role$"
        if re.match(pattern, role_arn):
            self._logger.debug("Valid Spot Fleet tagging role: %s", role_arn)
            return True
        return False

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

        # Add SpotFleet-specific context
        context.update(self._prepare_spotfleet_specific_context(template, request))

        return context

    def _prepare_spotfleet_specific_context(
        self, template: AWSTemplate, request: Request
    ) -> dict[str, Any]:
        """Prepare SpotFleet-specific context with template reference pattern."""

        # Base template data (referenced by all specs)
        base_launch_spec = {
            "image_id": template.image_id,
            "security_groups": template.security_group_ids or [],
        }

        # Instance type overrides (minimal data)
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
        else:
            # Single instance type
            single_type = (
                next(iter(template.machine_types.keys())) if template.machine_types else "t3.medium"
            )
            instance_overrides.append(
                {
                    "instance_type": single_type,
                    "weighted_capacity": 1,
                    "subnet_id": template.subnet_ids[0] if template.subnet_ids else None,
                }
            )

        abis_instance_requirements = template.get_instance_requirements_payload()
        has_abis = bool(abis_instance_requirements)

        return {
            # Fleet-specific values
            "fleet_name": f"{self.config_port.get_resource_prefix('spot_fleet')}{request.request_id}",
            # Template reference approach (fixes duplication)
            "base_launch_spec": base_launch_spec,
            "instance_overrides": instance_overrides,
            "has_overrides": len(instance_overrides) > 1,
            # Fleet configuration
            "fleet_role": template.fleet_role,
            "allocation_strategy": template.get_spot_fleet_allocation_strategy(),
            "instance_interruption_behavior": getattr(
                template, "instance_interruption_behavior", "terminate"
            ),
            "replace_unhealthy_instances": getattr(template, "replace_unhealthy_instances", True),
            # Pricing
            "spot_price": (
                str(template.max_price)
                if hasattr(template, "max_price") and template.max_price is not None
                else "0.10"
            ),
            "has_spot_price": hasattr(template, "max_price") and template.max_price is not None,
            # ABIS / InstanceRequirements
            "abis_instance_requirements": abis_instance_requirements,
            "has_abis": has_abis,
        }

    def _create_spot_fleet_config(
        self,
        template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Create Spot Fleet configuration with native spec support."""
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
                template, request, "spotfleet", context
            )
            if native_spec:
                # Ensure launch template info is in the spec
                if "LaunchSpecifications" in native_spec:
                    for spec in native_spec["LaunchSpecifications"]:
                        if "LaunchTemplate" not in spec:
                            spec["LaunchTemplate"] = {}
                        spec["LaunchTemplate"]["LaunchTemplateId"] = launch_template_id
                        spec["LaunchTemplate"]["Version"] = launch_template_version
                if template.abis_instance_requirements:
                    if "LaunchTemplateConfigs" in native_spec:
                        overrides = native_spec["LaunchTemplateConfigs"][0].get("Overrides", [])
                        if not any("InstanceRequirements" in o for o in overrides):
                            native_spec["LaunchTemplateConfigs"][0]["Overrides"] = [
                                {"InstanceRequirements": template.get_instance_requirements_payload()}
                            ]
                self._logger.info(
                    "Using native provider API spec with merge for SpotFleet template %s",
                    template.template_id,
                )
                return native_spec

            # Use template-driven approach with native spec service
            default_spec = self.aws_native_spec_service.render_default_spec("spotfleet", context)
            if template.abis_instance_requirements:
                if "LaunchTemplateConfigs" in default_spec:
                    overrides = default_spec["LaunchTemplateConfigs"][0].get("Overrides", [])
                    if not any("InstanceRequirements" in o for o in overrides):
                        default_spec["LaunchTemplateConfigs"][0]["Overrides"] = [
                            {"InstanceRequirements": template.get_instance_requirements_payload()}
                        ]
            return default_spec

        # Fallback to legacy logic when native spec service is not available
        return self._create_spot_fleet_config_legacy(
            template, request, launch_template_id, launch_template_version
        )

    def _create_spot_fleet_config_legacy(
        self,
        template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Create Spot Fleet configuration using legacy logic."""
        # Handle fleet role - convert EC2Fleet role to SpotFleet role if needed
        fleet_role = template.fleet_role

        # If using EC2Fleet service role, convert to SpotFleet service role
        if fleet_role and "ec2fleet.amazonaws.com/AWSServiceRoleForEC2Fleet" in fleet_role:
            account_id = self.aws_client.sts_client.get_caller_identity()["Account"]
            fleet_role = (
                f"arn:aws:iam::{account_id}:role/aws-service-role/"
                f"spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
            )
            self._logger.info("Converted EC2Fleet role to SpotFleet role: %s", fleet_role)
        elif fleet_role == "AWSServiceRoleForEC2SpotFleet":
            account_id = self.aws_client.sts_client.get_caller_identity()["Account"]
            fleet_role = (
                f"arn:aws:iam::{account_id}:role/aws-service-role/"
                f"spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
            )

        # Get package name for CreatedBy tag
        created_by = self._get_package_name()

        requested_count = int(getattr(request, "requested_count", 0) or 1)
        capacity_distribution = self._calculate_capacity_distribution(template, requested_count)
        target_capacity = capacity_distribution["target_capacity"]
        on_demand_capacity = capacity_distribution["on_demand_count"]

        # Common tags for both fleet and instances
        user_tags = [{"Key": "Name", "Value": f"hf-{request.request_id}"}]
        if template.tags:
            user_tags.extend([{"Key": k, "Value": v} for k, v in template.tags.items()])
        system_tags = build_system_tags(
            request_id=str(request.request_id),
            template_id=str(template.template_id),
            provider_api="SpotFleet",
        )
        common_tags = merge_tags(user_tags, system_tags)

        fleet_type_value = (
            template.fleet_type.value  # type: ignore[union-attr]
            if hasattr(template.fleet_type, "value")
            else template.fleet_type
        )
        fleet_config = {
            "LaunchTemplateConfigs": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    }
                }
            ],
            "TargetCapacity": target_capacity,
            "IamFleetRole": fleet_role,
            "AllocationStrategy": self._get_allocation_strategy(template.allocation_strategy or ""),
            "Type": fleet_type_value,
            "TagSpecifications": [
                {"ResourceType": "spot-fleet-request", "Tags": common_tags},
            ],
        }

        # Configure based on price type
        price_type = template.price_type or "spot"  # Default to spot for SpotFleet

        if price_type in ("ondemand", "heterogeneous") or on_demand_capacity > 0:
            # SpotFleet API: TargetCapacity is total, OnDemandTargetCapacity is on-demand portion
            fleet_config["OnDemandTargetCapacity"] = on_demand_capacity

        # Add fleet type specific configurations
        if template.fleet_type == AWSFleetType.MAINTAIN.value:
            fleet_config["ReplaceUnhealthyInstances"] = True
            fleet_config["TerminateInstancesWithExpiration"] = True

        # Add spot price if specified
        if template.max_price:
            fleet_config["SpotPrice"] = str(template.max_price)

        instance_requirements_payload = template.get_instance_requirements_payload()

        if instance_requirements_payload:
            overrides = []
            if template.subnet_ids:
                for subnet_id in template.subnet_ids:
                    overrides.append(
                        {
                            "SubnetId": subnet_id,
                            "InstanceRequirements": instance_requirements_payload,
                        }
                    )
            else:
                overrides.append({"InstanceRequirements": instance_requirements_payload})

            fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides
        else:
            from providers.aws.infrastructure.handlers.fleet_override_builder import (
                build_spot_fleet_overrides,
            )

            overrides = build_spot_fleet_overrides(
                template.machine_types,
                template.machine_types_ondemand,
                template.subnet_ids,
                template.max_price,
                template.price_type == "heterogeneous",
            )
            if overrides:
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides

        # Add Context field if specified
        if template.context:
            fleet_config["Context"] = template.context

        # Log the final configuration
        self._logger.debug("Spot Fleet configuration: %s", json.dumps(fleet_config, indent=2))

        return fleet_config

    def _get_allocation_strategy(self, strategy: str) -> str:
        """Convert Symphony allocation strategy to Spot Fleet allocation strategy."""
        if not strategy:
            return "lowestPrice"

        strategy_map = {
            "capacityOptimized": "capacityOptimized",
            "capacityOptimizedPrioritized": "capacityOptimizedPrioritized",
            "diversified": "diversified",
            "lowestPrice": "lowestPrice",
            "priceCapacityOptimized": "priceCapacityOptimized",
        }

        return strategy_map.get(strategy, "lowestPrice")

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Check the status of instances across all spot fleets in the request."""
        try:
            if not request.resource_ids:
                self._logger.info("No Spot Fleet Request IDs found in request")
                return []

            all_instances = []

            # Process all fleet IDs instead of just the first one
            for fleet_id in request.resource_ids:
                try:
                    fleet_instances = self._get_spot_fleet_instances(fleet_id)
                    if fleet_instances:
                        formatted_instances = self._format_instance_data(
                            fleet_instances, fleet_id, request
                        )
                        all_instances.extend(formatted_instances)
                except Exception as e:
                    self._logger.error("Failed to get instances for spot fleet %s: %s", fleet_id, e)
                    continue

            return all_instances

        except Exception as e:
            self._logger.error("Unexpected error checking Spot Fleet status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check Spot Fleet status: {e!s}")

    def _get_spot_fleet_instances(
        self,
        fleet_id: str,
        request_id: str = None,  # type: ignore[assignment]
    ) -> list[dict[str, Any]]:
        """Get instances for a specific spot fleet."""
        # Get fleet information
        fleet_list = self._retry_with_backoff(
            lambda: self._paginate(
                self.aws_client.ec2_client.describe_spot_fleet_requests,
                "SpotFleetRequestConfigs",
                SpotFleetRequestIds=[fleet_id],
            )
        )

        if not fleet_list:
            self._logger.warning("Spot Fleet Request %s not found", fleet_id)
            return []

        # Get active instances
        active_instances = self._retry_with_backoff(
            lambda fid=fleet_id: self._paginate(
                self.aws_client.ec2_client.describe_spot_fleet_instances,
                "ActiveInstances",
                SpotFleetRequestId=fid,
            )
        )

        if not active_instances:
            return []

        instance_ids = [instance["InstanceId"] for instance in active_instances]
        return self._get_instance_details(
            instance_ids, request_id=request_id, resource_id=fleet_id, provider_api="SpotFleet"
        )

    def _format_instance_data(
        self,
        instance_details: list[dict[str, Any]],
        resource_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        """Format Spot Fleet instance details to standard structure.

        instance_details is already snake_case from _get_instance_details.
        Just stamp handler-specific context fields.
        """
        metadata = getattr(request, "metadata", {}) or {}
        provider_api_value = metadata.get("provider_api", "SpotFleet")

        result = []
        for inst in instance_details:
            stamped = dict(inst)
            stamped.setdefault("resource_id", resource_id)
            stamped.setdefault("provider_api", provider_api_value)
            result.append(stamped)
        return result

    def release_hosts(  # type: ignore[override]
        self,
        machine_ids: list[str],
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
    ) -> None:
        """Release hosts across multiple Spot Fleets by detecting fleet membership.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: Dict mapping instance_id to (resource_id or None, desired_capacity)
        """
        try:
            if not machine_ids:
                self._logger.warning("No instance IDs provided for Spot Fleet termination")
                return

            self._logger.info("Releasing hosts for %d instances: %s", len(machine_ids), machine_ids)

            # Use resource_mapping if available, otherwise fall back to AWS API calls
            if resource_mapping:
                filtered_mapping = {
                    instance_id: resource_mapping.get(instance_id, (None, 0))
                    for instance_id in machine_ids
                }
                fleet_instance_groups = self._group_instances_by_spot_fleet_from_mapping(
                    machine_ids, filtered_mapping
                )
                self._logger.info(
                    f"Grouped instances by Spot Fleet using resource mapping: {fleet_instance_groups}"
                )
            else:
                # Fallback to AWS API calls when no resource mapping is provided
                self._logger.info("No resource mapping provided, falling back to AWS API calls")
                fleet_instance_groups = self._group_instances_by_spot_fleet(machine_ids)
                self._logger.info(
                    f"Grouped instances by Spot Fleet using AWS API: {fleet_instance_groups}"
                )

            # Process each Spot Fleet group separately
            fleet_errors: list[tuple[Optional[str], Exception]] = []
            for fleet_id, fleet_data in fleet_instance_groups.items():
                try:
                    if fleet_id is not None:
                        # Handle Spot Fleet instances using dedicated method (primary case)
                        self._release_hosts_for_single_spot_fleet(
                            fleet_id, fleet_data["instance_ids"], fleet_data["fleet_details"]
                        )
                    else:
                        # Handle non-Spot Fleet instances (fallback case)
                        instance_ids = fleet_data["instance_ids"]
                        if instance_ids:
                            self._logger.info(
                                f"Terminating {len(instance_ids)} non-Spot Fleet instances"
                            )
                            self.aws_ops.terminate_instances_with_fallback(
                                instance_ids, self._request_adapter, "non-Spot Fleet instances"
                            )
                            self._logger.info(
                                "Terminated non-Spot Fleet instances: %s", instance_ids
                            )
                except Exception as e:
                    self._logger.error("Failed to release fleet %s: %s", fleet_id, e, exc_info=True)
                    fleet_errors.append((fleet_id, e))

            if fleet_errors:
                failed_ids = [str(fid) for fid, _ in fleet_errors]
                raise AWSInfrastructureError(
                    f"Failed to release {len(fleet_errors)} fleet(s): {', '.join(failed_ids)}"
                )

        except AWSInfrastructureError:
            raise
        except Exception as e:
            self._logger.error("Failed to release Spot Fleet hosts: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release Spot Fleet hosts: {e!s}")

    def _group_instances_by_spot_fleet_from_mapping(
        self, machine_ids: list[str], resource_mapping: dict[str, tuple[Optional[str], int]]
    ) -> dict[Optional[str], dict]:
        """Group Spot Fleet instances using shared mixin logic."""
        return self._group_instances_from_mapping(machine_ids, resource_mapping)

    def _group_instances_by_spot_fleet(self, instance_ids: list[str]) -> dict[Optional[str], dict]:
        """Group Spot Fleet instances via AWS lookups."""
        return self._group_instances_direct(instance_ids)

    # FleetGroupingMixin hooks
    def _collect_groups_from_instances(
        self,
        instance_ids: list[str],
        groups: dict[Optional[str], dict],
        group_ids_to_fetch: set[str],
    ) -> None:
        """Populate Spot Fleet groups from AWS describe_instances lookups."""
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

                    spot_fleet_instance_ids = set()

                    for reservation in response.get("Reservations", []):
                        for instance in reservation.get("Instances", []):
                            instance_id = instance.get("InstanceId")
                            if not instance_id:
                                continue

                            spot_fleet_id = None
                            for tag in instance.get("Tags", []):
                                if tag.get("Key") == "aws:ec2spot:fleet-request-id":
                                    spot_fleet_id = tag.get("Value")
                                    break

                            if not spot_fleet_id and instance.get("InstanceLifecycle") == "spot":
                                spot_fleet_id = self._find_spot_fleet_for_instance(instance_id)

                            if spot_fleet_id:
                                self._add_instance_to_group(groups, spot_fleet_id, instance_id)
                                spot_fleet_instance_ids.add(instance_id)
                                group_ids_to_fetch.add(spot_fleet_id)

                    non_spot_instances = [
                        iid for iid in chunk if iid not in spot_fleet_instance_ids
                    ]
                    for iid in non_spot_instances:
                        self._add_non_group_instance(groups, iid)

                except Exception as exc:
                    self._logger.warning(
                        "Failed to describe Spot Fleet instances for chunk %s: %s", chunk, exc
                    )
                    for iid in chunk:
                        self._add_non_group_instance(groups, iid)

        except Exception as exc:
            self._logger.error("Failed to group instances by Spot Fleet: %s", exc)
            groups.clear()
            group_ids_to_fetch.clear()
            groups[None] = {"instance_ids": instance_ids.copy()}

    def _fetch_and_attach_group_details(
        self, group_ids: set[str], groups: dict[Optional[str], dict]
    ) -> None:
        """Fetch Spot Fleet request details for grouped fleets."""
        if not group_ids:
            return

        try:
            fleet_ids_list = list(group_ids)
            for fleet_chunk in self._chunk_list(fleet_ids_list, self.grouping_chunk_size):
                fleet_response = self._retry_with_backoff(
                    self.aws_client.ec2_client.describe_spot_fleet_requests,
                    operation_type="read_only",
                    SpotFleetRequestIds=fleet_chunk,
                )

                for fleet_details in fleet_response.get("SpotFleetRequestConfigs", []):
                    fleet_id = fleet_details.get("SpotFleetRequestId")
                    if fleet_id in groups:
                        groups[fleet_id]["fleet_details"] = fleet_details

        except Exception as exc:
            self._logger.warning("Failed to fetch Spot Fleet details: %s", exc)

    def _grouping_label(self) -> str:
        return "Spot Fleet"

    def _find_spot_fleet_for_instance(self, instance_id: str) -> Optional[str]:
        """
        Find the Spot Fleet request ID for a specific instance by querying active fleets.

        Args:
            instance_id: EC2 instance ID

        Returns:
            Spot Fleet request ID if found, None otherwise
        """
        try:
            # Get all active spot fleet requests
            response = self._retry_with_backoff(
                lambda: self._paginate(
                    self.aws_client.ec2_client.describe_spot_fleet_requests,
                    "SpotFleetRequestConfigs",
                    SpotFleetRequestStates=["active", "modifying"],
                ),
                operation_type="read_only",
            )

            # Check each fleet for the instance
            for fleet in response:
                fleet_id = fleet.get("SpotFleetRequestId")
                if not fleet_id:
                    continue

                try:
                    # Get instances for this fleet
                    fleet_instances = self._retry_with_backoff(
                        lambda fid=fleet_id: self._paginate(
                            self.aws_client.ec2_client.describe_spot_fleet_instances,
                            "ActiveInstances",
                            SpotFleetRequestId=fid,
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
            self._logger.debug(f"Failed to find Spot Fleet for instance {instance_id}: {e}")

        return None

    def _release_hosts_for_single_spot_fleet(
        self, fleet_id: str, fleet_instance_ids: list[str], fleet_details: dict
    ) -> None:
        """Release hosts for a single Spot Fleet with proper fleet management."""
        self._logger.info(
            f"Processing Spot Fleet {fleet_id} with {len(fleet_instance_ids)} instances"
        )

        try:
            if not fleet_details:
                fleet_response = self._retry_with_backoff(
                    self.aws_client.ec2_client.describe_spot_fleet_requests,
                    operation_type="read_only",
                    SpotFleetRequestIds=[fleet_id],
                )
                fleet_configs = fleet_response.get("SpotFleetRequestConfigs", [])
                fleet_details = fleet_configs[0] if fleet_configs else {}

            fleet_config = fleet_details.get("SpotFleetRequestConfig", {}) if fleet_details else {}
            fleet_type = str(fleet_config.get("Type", "maintain")).lower()
            target_capacity = int(
                fleet_config.get("TargetCapacity", len(fleet_instance_ids or [])) or 0
            )
            on_demand_capacity = int(fleet_config.get("OnDemandTargetCapacity", 0) or 0)
            new_target_capacity = None

            if fleet_instance_ids:
                if fleet_type == AWSFleetType.MAINTAIN:
                    new_target_capacity = max(0, target_capacity - len(fleet_instance_ids))
                    new_on_demand_capacity = min(on_demand_capacity, new_target_capacity)

                    self._logger.info(
                        "Reducing maintain Spot Fleet %s capacity from %s to %s before terminating instances",
                        fleet_id,
                        target_capacity,
                        new_target_capacity,
                    )

                    self._retry_with_backoff(
                        self.aws_client.ec2_client.modify_spot_fleet_request,
                        operation_type="critical",
                        SpotFleetRequestId=fleet_id,
                        TargetCapacity=new_target_capacity,
                        OnDemandTargetCapacity=new_on_demand_capacity,
                    )

                # Terminate specific instances using existing utility
                self.aws_ops.terminate_instances_with_fallback(
                    fleet_instance_ids, self._request_adapter, f"SpotFleet-{fleet_id} instances"
                )
                self._logger.info(
                    "Terminated Spot Fleet %s instances: %s", fleet_id, fleet_instance_ids
                )

                if fleet_type == AWSFleetType.MAINTAIN and new_target_capacity == 0:
                    self._logger.info(
                        "Maintain Spot Fleet %s capacity is zero, cancelling fleet", fleet_id
                    )
                    self._retry_with_backoff(
                        self.aws_client.ec2_client.cancel_spot_fleet_requests,
                        operation_type="critical",
                        SpotFleetRequestIds=[fleet_id],
                        TerminateInstances=False,
                    )
            else:
                # If no specific instances provided, cancel entire spot fleet
                self._retry_with_backoff(
                    self.aws_client.ec2_client.cancel_spot_fleet_requests,
                    operation_type="critical",
                    SpotFleetRequestIds=[fleet_id],
                    TerminateInstances=True,
                )
                self._logger.info("Cancelled entire Spot Fleet: %s", fleet_id)

        except Exception as e:
            self._logger.error("Failed to terminate spot fleet %s: %s", fleet_id, e)
            raise

    @staticmethod
    def _chunk_list(items: list[str], chunk_size: int):
        """Yield successive chunk-sized lists from items."""
        for index in range(0, len(items), chunk_size):
            yield items[index : index + chunk_size]

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Get example templates for SpotFleet handler."""
        return [
            # Request fleet type examples
            AWSTemplate(
                template_id="SpotFleet-Request-LowestPrice",
                name="Spot Fleet Request - Lowest Price",
                description="Spot Fleet request with lowest price allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                image_id="ami-12345678",
                max_instances=20,
                price_type="spot",
                allocation_strategy="lowestPrice",
                fleet_type="request",
                max_price=0.05,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "dev", "ManagedBy": "ORB"},
            ),
            AWSTemplate(
                template_id="SpotFleet-Request-Diversified",
                name="Spot Fleet Request - Diversified",
                description="Spot Fleet request with diversified allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                image_id="ami-12345678",
                max_instances=25,
                price_type="spot",
                allocation_strategy="diversified",
                fleet_type="request",
                max_price=0.06,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "dev", "ManagedBy": "ORB"},
            ),
            AWSTemplate(
                template_id="SpotFleet-Request-CapacityOptimized",
                name="Spot Fleet Request - Capacity Optimized",
                description="Spot Fleet request with capacity optimized allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                image_id="ami-12345678",
                max_instances=30,
                price_type="spot",
                allocation_strategy="capacityOptimized",
                fleet_type="request",
                max_price=0.07,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "dev", "ManagedBy": "ORB"},
            ),
            # Maintain fleet type examples
            AWSTemplate(
                template_id="SpotFleet-Maintain-LowestPrice",
                name="Spot Fleet Maintain - Lowest Price",
                description="Spot Fleet maintain with lowest price allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                image_id="ami-12345678",
                max_instances=15,
                price_type="spot",
                allocation_strategy="lowestPrice",
                fleet_type="maintain",
                max_price=0.04,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "prod", "ManagedBy": "ORB"},
            ),
            AWSTemplate(
                template_id="SpotFleet-Maintain-Diversified",
                name="Spot Fleet Maintain - Diversified",
                description="Spot Fleet maintain with diversified allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                image_id="ami-12345678",
                max_instances=20,
                price_type="spot",
                allocation_strategy="diversified",
                fleet_type="maintain",
                max_price=0.05,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "prod", "ManagedBy": "ORB"},
            ),
            AWSTemplate(
                template_id="SpotFleet-Maintain-CapacityOptimized",
                name="Spot Fleet Maintain - Capacity Optimized",
                description="Spot Fleet maintain with capacity optimized allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                image_id="ami-12345678",
                max_instances=25,
                price_type="spot",
                allocation_strategy="capacityOptimized",
                fleet_type="maintain",
                max_price=0.06,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "prod", "ManagedBy": "ORB"},
            ),
        ]
