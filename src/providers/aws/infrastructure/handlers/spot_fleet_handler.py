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
from datetime import datetime
from typing import Any, Dict, List

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.domain.template.value_objects import AWSFleetType
from providers.aws.exceptions.aws_exceptions import (
    AWSInfrastructureError,
    AWSValidationError,
    IAMError,
)
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from providers.aws.utilities.aws_operations import AWSOperations


@injectable
class SpotFleetHandler(AWSHandler):
    """Handler for Spot Fleet operations."""

    def __init__(
        self,
        aws_client,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: RequestAdapterPort = None,
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
        super().__init__(aws_client, logger, aws_ops, launch_template_manager, request_adapter)

        # Get AWS native spec service from container
        from infrastructure.di.container import get_container

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

    @handle_infrastructure_exceptions(context="spot_fleet_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> Dict[str, Any]:
        """
        Create a Spot Fleet to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            fleet_id = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_spot_fleet_internal(request, aws_template),
                operation_name="create Spot Fleet",
                context="SpotFleet",
            )

            return {
                "success": True,
                "resource_ids": [fleet_id],
                "instances": [],  # Spot Fleet instances come later
                "provider_data": {
                    "resource_type": "spot_fleet",
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

    def _create_spot_fleet_internal(self, request: Request, aws_template: AWSTemplate) -> str:
        """Create Spot Fleet with pure business logic."""
        # Validate Spot Fleet specific prerequisites
        self._validate_spot_prerequisites(aws_template)

        # Validate fleet type
        if not aws_template.fleet_type:
            raise AWSValidationError("Fleet type is required for SpotFleet")

        # Validate fleet type - SpotFleet supports REQUEST and MAINTAIN types
        valid_types = ["request", "maintain"]
        try:
            fleet_type_value = (
                aws_template.fleet_type.value
                if hasattr(aws_template.fleet_type, "value")
                else str(aws_template.fleet_type)
            )
            if fleet_type_value.lower() not in valid_types:
                raise ValueError  # Will be caught by the except block below
        except (ValueError, AttributeError):
            raise AWSValidationError(
                f"Invalid Spot fleet type: {aws_template.fleet_type}. "
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

        return fleet_id

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
        else:
            # For service-linked roles, we only validate the format
            if "AWSServiceRoleForEC2SpotFleet" in aws_template.fleet_role:
                if aws_template.fleet_role != "AWSServiceRoleForEC2SpotFleet":
                    errors.append(
                        f"Invalid Spot Fleet service-linked role format: {aws_template.fleet_role}"
                    )
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
                    errors.append(f"Invalid custom fleet role: {str(e)}")

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

        # For heterogeneous price type with vm_types_on_demand, validate the
        # configuration
        if (
            hasattr(aws_template, "price_type")
            and aws_template.price_type == "heterogeneous"
            and hasattr(aws_template, "vm_types_on_demand")
            and aws_template.vm_types_on_demand
        ):
            # Validate that instance_types is also specified
            if not hasattr(aws_template, "instance_types") or not aws_template.instance_types:
                errors.append("instance_types must be specified when using instance_types_ondemand")

            # Validate that instance_types_ondemand has valid instance types
            for instance_type, weight in aws_template.instance_types_ondemand.items():
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

    def _check_iam_permissions(self, role_arn: str) -> None:
        """
        Check if current credentials have necessary IAM permissions.

        Args:
            role_arn: The role ARN to validate permissions for

        Raises:
            IAMError: If permissions are insufficient
        """
        try:
            # Get current identity
            identity = self.aws_client.sts_client.get_caller_identity()

            # Check permissions - create IAM client directly from session
            iam_client = self.aws_client.session.client("iam", config=self.aws_client.boto_config)
            response = iam_client.simulate_principal_policy(
                PolicySourceArn=identity["Arn"],
                ActionNames=[
                    "ec2:RequestSpotFleet",
                    "ec2:ModifySpotFleetRequest",
                    "ec2:CancelSpotFleetRequests",
                    "ec2:DescribeSpotFleetRequests",
                    "ec2:DescribeSpotFleetInstances",
                    "iam:PassRole",
                ],
                ResourceArns=[role_arn],
            )

            # Check evaluation results
            for result in response["EvaluationResults"]:
                if result["EvalDecision"] != "allowed":
                    raise IAMError(f"Missing permission: {result['EvalActionName']}")

        except Exception as e:
            raise IAMError(f"Failed to validate IAM permissions: {str(e)}")

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> Dict[str, Any]:
        """Prepare context with all computed values for template rendering."""
        
        # Business logic: heterogeneous capacity calculations
        if template.price_type == "heterogeneous":
            percent_on_demand = template.percent_on_demand or 0
            on_demand_count = int(request.requested_count * percent_on_demand / 100)
            spot_count = request.requested_count - on_demand_count
        else:
            on_demand_count = 0
            spot_count = request.requested_count
        
        # Launch specifications computation
        launch_specs = []
        if template.instance_types and template.subnet_ids:
            for subnet_id in template.subnet_ids:
                for instance_type, weight in template.instance_types.items():
                    launch_specs.append({
                        "instance_type": instance_type,
                        "subnet_id": subnet_id,
                        "weighted_capacity": weight,
                        "image_id": template.image_id,
                        "security_groups": template.security_group_ids or []
                    })
        elif template.instance_types:
            for instance_type, weight in template.instance_types.items():
                launch_specs.append({
                    "instance_type": instance_type,
                    "weighted_capacity": weight,
                    "image_id": template.image_id,
                    "security_groups": template.security_group_ids or []
                })
        else:
            # Single instance type
            launch_specs.append({
                "instance_type": template.instance_type,
                "weighted_capacity": 1,
                "image_id": template.image_id,
                "security_groups": template.security_group_ids or [],
                "subnet_id": template.subnet_ids[0] if template.subnet_ids else None
            })
        
        # Get package name for CreatedBy tag
        created_by = "open-hostfactory-plugin"
        if hasattr(self, "config_port") and self.config_port:
            try:
                package_info = self.config_port.get_package_info()
                created_by = package_info.get("name", "open-hostfactory-plugin")
            except Exception:  # nosec B110
                pass
        
        # Process custom tags
        custom_tags = []
        if template.tags:
            custom_tags = [{"key": k, "value": v} for k, v in template.tags.items()]
        
        return {
            # Basic values
            "target_capacity": request.requested_count,
            "request_id": str(request.request_id),
            "template_id": str(template.template_id),
            
            # Computed business logic
            "on_demand_target_capacity": on_demand_count,
            "spot_target_capacity": spot_count,
            "launch_specifications": launch_specs,
            
            # Fleet configuration
            "fleet_role": template.fleet_role,
            "allocation_strategy": template.allocation_strategy or "lowestPrice",
            "instance_interruption_behavior": getattr(template, 'instance_interruption_behavior', 'terminate'),
            "replace_unhealthy_instances": getattr(template, 'replace_unhealthy_instances', True),
            
            # Conditional flags
            "is_heterogeneous": template.price_type == "heterogeneous",
            "has_multiple_specs": len(launch_specs) > 1,
            "has_spot_price": hasattr(template, 'max_price') and template.max_price is not None,
            "has_custom_tags": bool(custom_tags),
            
            # Pricing
            "spot_price": str(template.max_price) if hasattr(template, 'max_price') and template.max_price is not None else None,
            
            # Dynamic values
            "created_by": created_by,
            "timestamp": datetime.utcnow().isoformat(),
            "fleet_name": f"hf-spotfleet-{request.request_id}",
            "custom_tags": custom_tags
        }

    def _create_spot_fleet_config(
        self,
        template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> Dict[str, Any]:
        """Create Spot Fleet configuration with native spec support."""
        # Try native spec processing first
        if self.aws_native_spec_service:
            native_spec = self.aws_native_spec_service.process_provider_api_spec(template, request)
            if native_spec:
                # Merge launch template info into native spec
                if "LaunchSpecifications" in native_spec:
                    for spec in native_spec["LaunchSpecifications"]:
                        if "LaunchTemplate" not in spec:
                            spec["LaunchTemplate"] = {}
                        spec["LaunchTemplate"]["LaunchTemplateId"] = launch_template_id
                        spec["LaunchTemplate"]["Version"] = launch_template_version
                self._logger.info(
                    "Using native provider API spec for SpotFleet template %s", template.template_id
                )
                return native_spec

            # Use template-driven approach with native spec service
            context = self._prepare_template_context(template, request)
            context.update({
                "launch_template_id": launch_template_id,
                "launch_template_version": launch_template_version
            })
            
            return self.aws_native_spec_service.render_default_spec("spotfleet", context)
        
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
    ) -> Dict[str, Any]:
        """Create Spot Fleet configuration using legacy logic."""
        # Strip the full ARN for service-linked role
        fleet_role = template.fleet_role
        if fleet_role == "AWSServiceRoleForEC2SpotFleet":
            account_id = self.aws_client.sts_client.get_caller_identity()["Account"]
            fleet_role = (
                f"arn:aws:iam::{account_id}:role/aws-service-role/"
                f"spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
            )

        # Get package name for CreatedBy tag
        created_by = "open-hostfactory-plugin"  # fallback
        if hasattr(self, "config_port") and self.config_port:
            try:
                package_info = self.config_port.get_package_info()
                created_by = package_info.get("name", "open-hostfactory-plugin")
            except Exception:  # nosec B110
                # Intentionally silent fallback for package info retrieval
                pass

        # Common tags for both fleet and instances
        common_tags = [
            {"Key": "Name", "Value": f"hf-{request.request_id}"},
            {"Key": "RequestId", "Value": str(request.request_id)},
            {"Key": "TemplateId", "Value": str(template.template_id)},
            {"Key": "CreatedBy", "Value": created_by},
            {"Key": "CreatedAt", "Value": datetime.utcnow().isoformat()},
        ]

        fleet_config = {
            "LaunchTemplateConfigs": [
                {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    }
                }
            ],
            "TargetCapacity": request.requested_count,
            "IamFleetRole": fleet_role,
            "AllocationStrategy": self._get_allocation_strategy(template.allocation_strategy),
            "Type": template.fleet_type,
            "TagSpecifications": [{"ResourceType": "spot-fleet-request", "Tags": common_tags}],
        }

        # Configure based on price type
        price_type = template.price_type or "spot"  # Default to spot for SpotFleet

        if price_type == "ondemand":
            # For ondemand, set all capacity as on-demand
            fleet_config["OnDemandTargetCapacity"] = request.requested_count
            fleet_config["SpotTargetCapacity"] = 0
            fleet_config["DefaultTargetCapacity"] = "onDemand"

        elif price_type == "heterogeneous":
            # For heterogeneous, split capacity based on percent_on_demand
            percent_on_demand = template.percent_on_demand or 0
            on_demand_count = int(request.requested_count * percent_on_demand / 100)
            spot_count = request.requested_count - on_demand_count

            fleet_config["OnDemandTargetCapacity"] = on_demand_count
            fleet_config["SpotTargetCapacity"] = spot_count
            fleet_config["DefaultTargetCapacity"] = "spot"

        else:  # "spot" (default)
            # For spot, set all capacity as spot
            fleet_config["OnDemandTargetCapacity"] = 0
            fleet_config["SpotTargetCapacity"] = request.requested_count
            fleet_config["DefaultTargetCapacity"] = "spot"

        # Add template tags if any
        if template.tags:
            instance_tags = [{"Key": k, "Value": v} for k, v in template.tags.items()]
            fleet_config["TagSpecifications"][0]["Tags"].extend(instance_tags)

        # Add fleet type specific configurations
        if template.fleet_type == AWSFleetType.MAINTAIN.value:
            fleet_config["ReplaceUnhealthyInstances"] = True
            fleet_config["TerminateInstancesWithExpiration"] = True

        # Add spot price if specified
        if template.max_price:
            fleet_config["SpotPrice"] = str(template.max_price)

        # Add instance type overrides if specified
        if template.instance_types:
            # For heterogeneous price type with on-demand instances
            if template.price_type == "heterogeneous" and template.instance_types_ondemand:
                # Create spot instance overrides
                spot_overrides = [
                    {
                        "InstanceType": instance_type,
                        "WeightedCapacity": weight,
                        "Priority": idx + 1,
                        "SpotPrice": (str(template.max_price) if template.max_price else None),
                    }
                    for idx, (instance_type, weight) in enumerate(template.instance_types.items())
                ]

                # Create on-demand instance overrides
                ondemand_overrides = [
                    {
                        "InstanceType": instance_type,
                        "WeightedCapacity": weight,
                        "Priority": idx + len(template.instance_types) + 1,
                        # Force this to be on-demand by not specifying SpotPrice
                    }
                    for idx, (instance_type, weight) in enumerate(
                        template.instance_types_ondemand.items()
                    )
                ]

                # Combine both types of overrides
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = (
                    spot_overrides + ondemand_overrides
                )

                # Log the combined overrides
                self._logger.debug(
                    "Created combined overrides for heterogeneous fleet: "
                    f"{len(spot_overrides)} spot instance types, "
                    f"{len(ondemand_overrides)} on-demand instance types"
                )
            else:
                # Standard spot instance overrides
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = [
                    {
                        "InstanceType": instance_type,
                        "WeightedCapacity": weight,
                        "Priority": idx + 1,
                        "SpotPrice": (str(template.max_price) if template.max_price else None),
                    }
                    for idx, (instance_type, weight) in enumerate(template.instance_types.items())
                ]

        # Add subnet configuration
        if template.subnet_ids:
            if "Overrides" not in fleet_config["LaunchTemplateConfigs"][0]:
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = []

            # For heterogeneous price type with on-demand instances
            if (
                template.price_type == "heterogeneous"
                and template.instance_types_ondemand
                and template.instance_types
            ):
                # Create spot instance overrides with subnets
                spot_overrides = []
                for subnet_id in template.subnet_ids:
                    for idx, (instance_type, weight) in enumerate(template.instance_types.items()):
                        override = {
                            "SubnetId": subnet_id,
                            "InstanceType": instance_type,
                            "WeightedCapacity": weight,
                            "Priority": idx + 1,
                            "SpotPrice": (str(template.max_price) if template.max_price else None),
                        }
                        spot_overrides.append(override)

                # Create on-demand instance overrides with subnets
                ondemand_overrides = []
                for subnet_id in template.subnet_ids:
                    for idx, (instance_type, weight) in enumerate(
                        template.instance_types_ondemand.items()
                    ):
                        override = {
                            "SubnetId": subnet_id,
                            "InstanceType": instance_type,
                            "WeightedCapacity": weight,
                            "Priority": idx + len(template.instance_types) + 1,
                            # No SpotPrice for on-demand instances
                        }
                        ondemand_overrides.append(override)

                # Combine both types of overrides
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = (
                    spot_overrides + ondemand_overrides
                )

                # Log the combined overrides
                self._logger.debug(
                    "Created combined overrides with subnets for heterogeneous fleet: "
                    f"{len(spot_overrides)} spot instance overrides, "
                    f"{len(ondemand_overrides)} on-demand instance overrides"
                )
            # If we have both instance types and subnets, create all combinations
            elif template.instance_types:
                overrides = []
                for subnet_id in template.subnet_ids:
                    for idx, (instance_type, weight) in enumerate(template.instance_types.items()):
                        override = {
                            "SubnetId": subnet_id,
                            "InstanceType": instance_type,
                            "WeightedCapacity": weight,
                            "Priority": idx + 1,
                        }
                        if template.max_price:
                            override["SpotPrice"] = str(template.max_price)
                        overrides.append(override)
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = overrides
            else:
                fleet_config["LaunchTemplateConfigs"][0]["Overrides"] = [
                    {"SubnetId": subnet_id} for subnet_id in template.subnet_ids
                ]

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

    def _monitor_spot_prices(self, aws_template: AWSTemplate) -> Dict[str, float]:
        """Monitor current spot prices in specified regions/AZs."""
        try:
            prices = {}
            instance_types = []

            # Get instance types from template
            if hasattr(aws_template, "instance_types") and aws_template.instance_types:
                instance_types = list(aws_template.instance_types.keys())
            elif hasattr(aws_template, "instance_type") and aws_template.instance_type:
                instance_types = [aws_template.instance_type]

            if not instance_types:
                self._logger.warning("No instance types found for spot price monitoring")
                return {}

            price_history = self._retry_with_backoff(
                lambda: self._paginate(
                    self.aws_client.ec2_client.describe_spot_price_history,
                    "SpotPriceHistory",
                    InstanceTypes=instance_types,
                    ProductDescriptions=["Linux/UNIX"],
                )
            )

            for price in price_history:
                key = f"{price['InstanceType']}-{price['AvailabilityZone']}"
                prices[key] = float(price["SpotPrice"])

            return prices

        except Exception as e:
            self._logger.warning("Failed to monitor spot prices: %s", str(e))
            return {}

    def check_hosts_status(self, request: Request) -> List[Dict[str, Any]]:
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
                        formatted_instances = self._format_instance_data(fleet_instances, fleet_id)
                        all_instances.extend(formatted_instances)
                except Exception as e:
                    self._logger.error("Failed to get instances for spot fleet %s: %s", fleet_id, e)
                    continue

            return all_instances

        except Exception as e:
            self._logger.error("Unexpected error checking Spot Fleet status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check Spot Fleet status: {str(e)}")

    def _get_spot_fleet_instances(self, fleet_id: str) -> List[Dict[str, Any]]:
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
        return self._get_instance_details(instance_ids)

    def release_hosts(self, request: Request) -> None:
        """Release hosts across all spot fleets in the request."""
        try:
            if not request.resource_ids:
                raise AWSInfrastructureError("No Spot Fleet Request IDs found in request")

            # Process all fleet IDs instead of just the first one
            for fleet_id in request.resource_ids:
                try:
                    if request.machine_references:
                        # Terminate specific instances using existing utility
                        instance_ids = [m.machine_id for m in request.machine_references]
                        self.aws_ops.terminate_instances_with_fallback(
                            instance_ids=instance_ids,
                            context=f"SpotFleet-{fleet_id}",
                        )
                    else:
                        # Cancel entire spot fleet
                        self._retry_with_backoff(
                            lambda fid=fleet_id: self.aws_client.ec2_client.cancel_spot_fleet_requests(
                                SpotFleetRequestIds=[fid], TerminateInstances=True
                            ),
                            operation_type="critical",
                        )
                        self._logger.info("Cancelled Spot Fleet: %s", fleet_id)
                except Exception as e:
                    self._logger.error("Failed to terminate spot fleet %s: %s", fleet_id, e)
                    continue

        except Exception as e:
            self._logger.error("Failed to release Spot Fleet hosts: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release Spot Fleet hosts: {str(e)}")
