"""AWS Auto Scaling Group Handler.

This module provides the Auto Scaling Group (ASG) handler implementation for
managing AWS Auto Scaling Groups through the AWS Auto Scaling API.

The ASG handler enables automatic scaling of EC2 instances based on demand,
health checks, and scaling policies, providing high availability and
cost optimization for long-running workloads.

Key Features:
    - Automatic scaling based on metrics
    - Health check and replacement
    - Multi-AZ deployment support
    - Launch template integration
    - Scaling policies and schedules

Classes:
    ASGHandler: Main handler for Auto Scaling Group operations

Usage:
    This handler is used by the AWS provider to manage Auto Scaling Groups
    for workloads that require automatic scaling and high availability.

Note:
    ASG is ideal for long-running services that need to scale automatically
    based on demand and maintain high availability across multiple AZs.
"""

from typing import Any, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.utilities.common.resource_naming import get_resource_prefix
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.handlers.base_context_mixin import BaseContextMixin
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.infrastructure.handlers.fleet_grouping_mixin import FleetGroupingMixin
from providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from providers.aws.utilities.aws_operations import AWSOperations


@injectable
class ASGHandler(AWSHandler, BaseContextMixin, FleetGroupingMixin):
    """Handler for Auto Scaling Group operations."""

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
        Initialize the ASG handler with integrated dependencies.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            aws_ops: AWS operations utility
            launch_template_manager: Launch template manager for AWS-specific operations
            request_adapter: Optional request adapter for terminating instances
        """
        # Use integrated base class initialization
        super().__init__(
            aws_client,
            logger,
            aws_ops,
            launch_template_manager,
            request_adapter,
            machine_adapter,
        )

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

            if self._machine_adapter is None:
                from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter

                self._machine_adapter = container.get(AWSMachineAdapter)
        except Exception:
            # Service not available, native specs disabled
            self.aws_native_spec_service = None
            self.config_port = None

    @handle_infrastructure_exceptions(context="asg_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> dict[str, Any]:
        """
        Create an Auto Scaling Group to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            asg_name = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_asg_internal(request, aws_template),
                operation_name="create Auto Scaling Group",
                context="ASG",
            )

            return {
                "success": True,
                "resource_ids": [asg_name],
                "instances": [],  # ASG instances come later
                "provider_data": {"resource_type": "asg"},
            }
        except Exception as e:
            return {
                "success": False,
                "resource_ids": [],
                "instances": [],
                "error_message": str(e),
            }

    def _validate_asg_prerequisites(self, template: AWSTemplate) -> None:
        """Validate ASG-specific prerequisites."""
        errors = {}

        # ASG requires at least one subnet
        if not template.subnet_ids:
            errors["subnet_ids"] = "At least one subnet ID is required for Auto Scaling Groups"

        # ASG requires security groups
        if not template.security_group_ids:
            errors["security_group_ids"] = "Security group IDs are required for Auto Scaling Groups"

        if errors:
            error_details = []
            for field, message in errors.items():
                error_details.append(f"{field}: {message}")
            detailed_message = f"ASG template validation failed - {'; '.join(error_details)}"
            raise AWSInfrastructureError(detailed_message, errors)

    def _create_asg_internal(self, request: Request, aws_template: AWSTemplate) -> str:
        """Create ASG with pure business logic."""
        # Validate ASG specific prerequisites
        self._validate_asg_prerequisites(aws_template)

        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
        )

        # Store launch template info in request (if request has this method)
        if hasattr(request, "set_launch_template_info"):
            request.set_launch_template_info(
                launch_template_result.template_id, launch_template_result.version
            )

        # Generate ASG name
        asg_name = f"hf-{request.request_id}"

        # Create ASG configuration
        asg_config = self._create_asg_config(
            asg_name=asg_name,
            aws_template=aws_template,
            request=request,
            launch_template_id=launch_template_result.template_id,
            launch_template_version=launch_template_result.version,
        )

        # Create the ASG with circuit breaker for critical operation
        self._retry_with_backoff(
            self.aws_client.autoscaling_client.create_auto_scaling_group,
            operation_type="critical",
            **asg_config,
        )

        self._logger.info("Successfully created Auto Scaling Group: %s", asg_name)

        # Add ASG tags
        self._tag_asg(asg_name, aws_template, str(request.request_id))

        # Enable instance protection if specified
        if hasattr(aws_template, "instance_protection") and aws_template.instance_protection:
            self._enable_instance_protection(asg_name)

        # Set instance lifecycle hooks if needed
        if hasattr(aws_template, "lifecycle_hooks") and aws_template.lifecycle_hooks:
            self._set_lifecycle_hooks(asg_name, aws_template.lifecycle_hooks)

        return asg_name

    def _tag_asg(self, asg_name: str, aws_template: AWSTemplate, request_id: str) -> None:
        """Add tags to the Auto Scaling Group."""
        try:
            created_by = self._get_package_name()

            # Prepare standard tags with proper ResourceId and ResourceType
            tags = [
                {
                    "Key": "Name",
                    "Value": f"hostfactory-asg-{request_id}",
                    "PropagateAtLaunch": True,
                    "ResourceId": asg_name,
                    "ResourceType": "auto-scaling-group",
                },
                {
                    "Key": "RequestId",
                    "Value": str(request_id),
                    "PropagateAtLaunch": True,
                    "ResourceId": asg_name,
                    "ResourceType": "auto-scaling-group",
                },
                {
                    "Key": "TemplateId",
                    "Value": aws_template.template_id,
                    "PropagateAtLaunch": True,
                    "ResourceId": asg_name,
                    "ResourceType": "auto-scaling-group",
                },
                {
                    "Key": "CreatedBy",
                    "Value": created_by,
                    "PropagateAtLaunch": True,
                    "ResourceId": asg_name,
                    "ResourceType": "auto-scaling-group",
                },
                {
                    "Key": "ProviderApi",
                    "Value": "ASG",
                    "PropagateAtLaunch": True,
                    "ResourceId": asg_name,
                    "ResourceType": "auto-scaling-group",
                },
            ]

            # Add custom tags from template
            if hasattr(aws_template, "tags") and aws_template.tags:
                for key, value in aws_template.tags.items():
                    tags.append(
                        {
                            "Key": key,
                            "Value": str(value),
                            "PropagateAtLaunch": True,
                            "ResourceId": asg_name,
                            "ResourceType": "auto-scaling-group",
                        }
                    )

            # Create tags for the ASG
            self._retry_with_backoff(
                self.aws_client.autoscaling_client.create_or_update_tags,
                operation_type="critical",
                Tags=tags,
            )

            self._logger.info("Successfully tagged ASG %s", asg_name)
        except Exception as e:
            self._logger.warning("Failed to tag ASG %s: %s", asg_name, e)
            # Don't fail the entire operation if tagging fails
            pass

    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> dict[str, Any]:
        """Prepare context with all computed values for template rendering."""

        # Start with base context
        context = self._prepare_base_context(
            template,
            str(request.request_id),
            request.requested_count,
        )

        # Add capacity distribution (for consistency, even if not all used)
        context.update(self._calculate_capacity_distribution(template, request.requested_count))

        # Add standard flags
        context.update(self._prepare_standard_flags(template))

        # Add standard tags
        tag_context = self._prepare_standard_tags(template, str(request.request_id))
        context.update(tag_context)

        # Add ASG-specific context
        context.update(self._prepare_asg_specific_context(template, request))

        return context

    def _prepare_asg_specific_context(
        self, template: AWSTemplate, request: Request
    ) -> dict[str, Any]:
        """Prepare ASG-specific context."""

        return {
            # ASG-specific values
            "asg_name": f"{get_resource_prefix('asg')}{request.request_id}",
            "min_size": 0,
            "max_size": request.requested_count * 2,  # Allow buffer
            # Configuration values
            "default_cooldown": 300,
            "health_check_type": "EC2",
            "health_check_grace_period": 300,
            "vpc_zone_identifier": ",".join(template.subnet_ids) if template.subnet_ids else None,
            "new_instances_protected_from_scale_in": True,
            "context": (
                template.context if hasattr(template, "context") and template.context else None
            ),
            # ASG-specific flags
            "has_context": hasattr(template, "context") and bool(template.context),
            "has_instance_protection": hasattr(template, "instance_protection")
            and template.instance_protection,
            "has_lifecycle_hooks": hasattr(template, "lifecycle_hooks")
            and bool(template.lifecycle_hooks),
        }

    def _create_asg_config(
        self,
        asg_name: str,
        aws_template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Create Auto Scaling Group configuration with native spec support."""
        # Try native spec processing with merge support
        if self.aws_native_spec_service:
            context = self._prepare_template_context(aws_template, request)
            context.update(
                {
                    "launch_template_id": launch_template_id,
                    "launch_template_version": launch_template_version,
                    "asg_name": asg_name,
                    "new_instances_protected_from_scale_in": True,
                }
            )

            native_spec = self.aws_native_spec_service.process_provider_api_spec_with_merge(
                aws_template, request, "asg", context
            )
            if native_spec:
                # Ensure launch template info is in the spec
                if "LaunchTemplate" not in native_spec:
                    native_spec["LaunchTemplate"] = {}
                native_spec["LaunchTemplate"]["LaunchTemplateId"] = launch_template_id
                native_spec["LaunchTemplate"]["Version"] = launch_template_version
                native_spec["AutoScalingGroupName"] = asg_name
                native_spec.setdefault("NewInstancesProtectedFromScaleIn", True)
                self._logger.info(
                    "Using native provider API spec with merge for ASG template %s",
                    aws_template.template_id,
                )
                return native_spec

            # Use template-driven approach with native spec service
            return self.aws_native_spec_service.render_default_spec("asg", context)

        # Fallback to legacy logic when native spec service is not available
        return self._create_asg_config_legacy(
            asg_name, aws_template, request, launch_template_id, launch_template_version
        )

    def _create_asg_config_legacy(
        self,
        asg_name: str,
        aws_template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Create Auto Scaling Group configuration using legacy logic."""
        asg_config: dict[str, Any] = {
            "AutoScalingGroupName": asg_name,
            "MinSize": 0,
            "MaxSize": request.requested_count * 2,  # Allow some buffer
            "DesiredCapacity": request.requested_count,
            "DefaultCooldown": 300,
            "HealthCheckType": "EC2",
            "HealthCheckGracePeriod": 300,
            "NewInstancesProtectedFromScaleIn": True,
        }

        # Prefer multi-instance maps (including legacy vm_types) over a single instance_type
        instance_types_map = getattr(aws_template, "instance_types", None) or getattr(
            aws_template, "vm_types", {}
        )

        # Prefer ABIS/InstanceRequirements payload when present (no explicit types)
        instance_requirements_payload = aws_template.get_instance_requirements_payload()
        if instance_requirements_payload:
            asg_config["MixedInstancesPolicy"] = {
                "LaunchTemplate": {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    },
                    "Overrides": [{"InstanceRequirements": instance_requirements_payload}],
                }
            }
        # Otherwise, emit explicit instance type overrides when we have a type map
        elif instance_types_map:
            overrides = []
            for itype, weight in instance_types_map.items():
                override = {"InstanceType": itype}
                if weight:
                    override["WeightedCapacity"] = str(weight)
                overrides.append(override)

            asg_config["MixedInstancesPolicy"] = {
                "LaunchTemplate": {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    },
                    "Overrides": overrides,
                }
            }
        else:
            # Fallback: single instance type (or none) uses plain launch template
            asg_config["LaunchTemplate"] = {
                "LaunchTemplateId": launch_template_id,
                "Version": launch_template_version,
            }

        # Add spot/on-demand distribution when spot pricing or mixed capacity requested
        price_type = getattr(aws_template, "price_type", "ondemand") or "ondemand"
        percent_on_demand = getattr(aws_template, "percent_on_demand", None)
        needs_spot_distribution = percent_on_demand is not None or price_type in (
            "spot",
            "heterogeneous",
        )

        if needs_spot_distribution:
            # Ensure MixedInstancesPolicy exists so we can attach distribution
            if "MixedInstancesPolicy" not in asg_config:
                asg_config["MixedInstancesPolicy"] = {
                    "LaunchTemplate": {
                        "LaunchTemplateSpecification": {
                            "LaunchTemplateId": launch_template_id,
                            "Version": launch_template_version,
                        }
                    }
                }

            ondemand_pct = int(percent_on_demand) if percent_on_demand is not None else 0
            asg_config["MixedInstancesPolicy"]["InstancesDistribution"] = {
                "OnDemandBaseCapacity": 0,
                "OnDemandPercentageAboveBaseCapacity": ondemand_pct,
            }

            if getattr(aws_template, "allocation_strategy", None):
                asg_config["MixedInstancesPolicy"]["InstancesDistribution"][
                    "SpotAllocationStrategy"
                ] = aws_template.get_asg_allocation_strategy()

            # When MixedInstancesPolicy is present, AWS requires launch settings to live there.
            # Remove top-level LaunchTemplate to avoid API validation errors.
            if "LaunchTemplate" in asg_config:
                asg_config.pop("LaunchTemplate", None)

        # Add subnet configuration
        if aws_template.subnet_ids:
            asg_config["VPCZoneIdentifier"] = ",".join(aws_template.subnet_ids)

        # Add Context field if specified
        if aws_template.context:
            asg_config["Context"] = aws_template.context

        return asg_config

    @handle_infrastructure_exceptions(context="asg_termination")
    def _get_asg_instances(self, asg_name: str) -> list[dict[str, Any]]:
        """Get instances for a specific ASG using DescribeAutoScalingInstances pagination."""
        # Collect all membership entries across pages
        asg_instances = self._retry_with_backoff(
            lambda: self._paginate(
                self.aws_client.autoscaling_client.describe_auto_scaling_instances,
                "AutoScalingInstances",
            )
        )

        instance_ids = [
            entry.get("InstanceId")
            for entry in asg_instances
            if entry.get("AutoScalingGroupName") == asg_name and entry.get("InstanceId")
        ]

        if not instance_ids:
            self._logger.warning("ASG %s not found or has no instances", asg_name)
            return []

        return self._get_instance_details(instance_ids)

    def _format_instance_data(
        self,
        instance_details: list[dict[str, Any]],
        resource_id: str,
        request: Request,
    ) -> list[dict[str, Any]]:
        """Format ASG instance details to standard structure."""
        metadata = getattr(request, "metadata", {}) or {}
        provider_api_value = metadata.get("provider_api", "ASG")

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

    @staticmethod
    def detect_asg_instances(aws_client, instance_ids: list[str]) -> dict[str, list[str]]:
        """
        Detect which instances belong to ASGs and group them.

        Args:
            aws_client: AWS client instance
            instance_ids: List of EC2 instance IDs

        Returns:
            Dictionary mapping ASG names to instance IDs, empty if no ASG instances found
        """
        try:
            asg_mapping = {}

            # Process instances in chunks to avoid API limits
            for chunk in ASGHandler._chunk_list(instance_ids, 50):
                try:
                    response = aws_client.autoscaling_client.describe_auto_scaling_instances(
                        InstanceIds=chunk
                    )

                    # Group instances by ASG
                    for entry in response.get("AutoScalingInstances", []):
                        instance_id = entry.get("InstanceId")
                        asg_name = entry.get("AutoScalingGroupName")

                        if instance_id and asg_name:
                            if asg_name not in asg_mapping:
                                asg_mapping[asg_name] = []
                            asg_mapping[asg_name].append(instance_id)

                except Exception:
                    # Skip failed chunks
                    continue

            return asg_mapping

        except Exception:
            return {}

    @staticmethod
    def _chunk_list(items: list[str], chunk_size: int):
        """Yield successive chunk-sized lists from items."""
        for index in range(0, len(items), chunk_size):
            yield items[index : index + chunk_size]

    def reduce_capacity_for_instance_ids(self, instance_ids: list[str]) -> None:
        """Reduce ASG capacity ahead of instance termination to avoid replacements."""
        if not instance_ids:
            return

        instance_group_map: dict[str, list[str]] = {}

        try:
            for chunk in self._chunk_list(instance_ids, 50):
                response = self._retry_with_backoff(
                    self.aws_client.autoscaling_client.describe_auto_scaling_instances,
                    operation_type="read_only",
                    InstanceIds=chunk,
                )

                for entry in response.get("AutoScalingInstances", []):
                    group_name = entry.get("AutoScalingGroupName")
                    instance_id = entry.get("InstanceId")
                    if group_name and instance_id:
                        instance_group_map.setdefault(group_name, []).append(instance_id)
        except Exception as exc:
            self._logger.warning("Failed to map instances to ASGs for capacity reduction: %s", exc)
            return

        if not instance_group_map:
            return

        for group_name, instances in instance_group_map.items():
            try:
                group_response = self._retry_with_backoff(
                    self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                    operation_type="read_only",
                    AutoScalingGroupNames=[group_name],
                )
            except Exception as exc:
                self._logger.warning(
                    "Failed to describe ASG %s while reducing capacity: %s", group_name, exc
                )
                continue

            groups = group_response.get("AutoScalingGroups", [])
            if not groups:
                continue

            asg = groups[0]
            current_desired = asg.get("DesiredCapacity", 0) or 0
            current_min = asg.get("MinSize", 0) or 0

            new_desired = max(0, current_desired - len(instances))
            new_min = min(current_min, new_desired)

            if current_desired == new_desired and current_min == new_min:
                continue

            try:
                self._retry_with_backoff(
                    self.aws_client.autoscaling_client.update_auto_scaling_group,
                    operation_type="critical",
                    AutoScalingGroupName=group_name,
                    DesiredCapacity=new_desired,
                    MinSize=new_min,
                )
                self._logger.info(
                    "Reduced ASG %s capacity from %s to %s before terminating %s instances",
                    group_name,
                    current_desired,
                    new_desired,
                    len(instances),
                )
            except Exception as exc:
                self._logger.warning(
                    "Failed to update ASG %s capacity prior to termination: %s",
                    group_name,
                    exc,
                )

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
    ) -> None:
        """Release hosts across multiple ASGs by detecting ASG membership.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: Dict mapping instance_id to (resource_id or None, desired_capacity) for intelligent resource management
        """
        try:
            if not machine_ids:
                self._logger.warning("No instance IDs provided for ASG termination")
                return

            self._logger.info("Releasing hosts for %d instances: %s", len(machine_ids), machine_ids)

            # Always use resource_mapping when available, but check each entry individually
            if resource_mapping:
                # Ensure every requested instance has an entry (fallback to AWS lookup when missing)
                complete_resource_mapping = {
                    instance_id: resource_mapping.get(instance_id, (None, 0))
                    for instance_id in machine_ids
                }
                missing = [
                    iid
                    for iid, (resource_id, _) in complete_resource_mapping.items()
                    if resource_id is None
                ]
                for instance_id in missing:
                    self._logger.debug(
                        "Instance %s not in resource mapping, will use AWS API lookup", instance_id
                    )

                asg_instance_groups = self._group_instances_by_asg_from_mapping(
                    complete_resource_mapping
                )
                self._logger.info(
                    f"Grouped instances by ASG using resource mapping: {asg_instance_groups}"
                )
            else:
                # Fallback to AWS API calls when no resource mapping is provided
                self._logger.info("No resource mapping provided, falling back to AWS API calls")
                asg_instance_groups = self._group_instances_by_asg(machine_ids)
                self._logger.info(f"Grouped instances by ASG using AWS API: {asg_instance_groups}")

            # Process each ASG group separately
            for asg_name, asg_data in asg_instance_groups.items():
                if asg_name is not None:
                    # Handle ASG instances using dedicated method (primary case)
                    self._release_hosts_for_single_asg(
                        asg_name, asg_data["instance_ids"], asg_data["asg_details"]
                    )
                else:
                    # Handle non-ASG instances (fallback case)
                    instance_ids = asg_data["instance_ids"]
                    if instance_ids:
                        self._logger.info(f"Terminating {len(instance_ids)} non-ASG instances")
                        self.aws_ops.terminate_instances_with_fallback(
                            instance_ids, self._request_adapter, "non-ASG instances"
                        )
                        self._logger.info("Terminated non-ASG instances: %s", instance_ids)

        except Exception as e:
            self._logger.error("Failed to release ASG hosts: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release ASG hosts: {e!s}")

    def _release_hosts_for_single_asg(
        self, asg_name: str, asg_instance_ids: list[str], asg_details: dict
    ) -> None:
        """Release hosts for a single ASG with proper capacity management."""
        self._logger.info(f"Processing ASG {asg_name} with {len(asg_instance_ids)} instances")

        # If ASG details are missing, still terminate the instances but skip ASG-specific operations
        if not asg_details:
            self._logger.warning(
                f"ASG details missing for {asg_name}, terminating instances without ASG operations"
            )
            self._logger.warning(
                f"ASG details missing for {asg_name}, terminating instances without ASG operations"
            )
            # Still terminate the instances even if ASG details are missing
            self.aws_ops.terminate_instances_with_fallback(
                asg_instance_ids,
                self._request_adapter,
                f"ASG {asg_name} instances (no ASG details)",
            )
            self._logger.info(
                "Terminated ASG %s instances without ASG operations: %s", asg_name, asg_instance_ids
            )
            self._logger.info(
                "Terminated ASG %s instances without ASG operations: %s", asg_name, asg_instance_ids
            )
            return

        # Detach instances from ASG first (API limit: 50 instance IDs per call, use 20 for safety)
        for chunk in self._chunk_list(asg_instance_ids, 20):
            self._retry_with_backoff(
                self.aws_client.autoscaling_client.detach_instances,
                operation_type="critical",
                AutoScalingGroupName=asg_name,
                InstanceIds=chunk,
                ShouldDecrementDesiredCapacity=True,
            )
            self._logger.debug("Detached chunk from ASG %s: %s", asg_name, chunk)
        self._logger.info("Detached instances from ASG %s: %s", asg_name, asg_instance_ids)

        # Then reduce desired capacity
        current_capacity = asg_details["DesiredCapacity"]
        new_capacity = max(0, current_capacity - len(asg_instance_ids))

        self._retry_with_backoff(
            self.aws_client.autoscaling_client.update_auto_scaling_group,
            operation_type="critical",
            AutoScalingGroupName=asg_name,
            DesiredCapacity=new_capacity,
            MinSize=min(new_capacity, asg_details["MinSize"]),
        )
        self._logger.info("Reduced ASG %s capacity to %s", asg_name, new_capacity)

        # Use consolidated AWS operations utility for instance termination
        self.aws_ops.terminate_instances_with_fallback(
            asg_instance_ids, self._request_adapter, f"ASG {asg_name} instances"
        )
        self._logger.info("Terminated ASG %s instances: %s", asg_name, asg_instance_ids)

        # If desired capacity has reached zero, delete the ASG
        if new_capacity == 0:
            self._logger.info("ASG %s capacity is zero, deleting ASG", asg_name)
            self._delete_asg(asg_name)

    def _delete_asg(self, asg_name: str) -> None:
        """Delete an Auto Scaling Group when it's no longer needed."""
        try:
            self._logger.info(f"Deleting ASG {asg_name}")

            # Delete the ASG with force delete to handle any remaining instances
            self._retry_with_backoff(
                self.aws_client.autoscaling_client.delete_auto_scaling_group,
                operation_type="critical",
                AutoScalingGroupName=asg_name,
                ForceDelete=True,
            )

            self._logger.info(f"Successfully deleted ASG {asg_name}")

        except Exception as e:
            # Log the error but don't fail the entire operation
            # ASG deletion is cleanup - the main termination should still succeed
            self._logger.warning(f"Failed to delete ASG {asg_name}: {e}")
            self._logger.warning(
                "ASG deletion failed, but instance termination completed successfully"
            )

    def _group_instances_by_asg_from_mapping(
        self, resource_mapping: dict[str, tuple[Optional[str], int]]
    ) -> dict[Optional[str], dict]:
        """Group ASG instances using shared mixin logic."""
        instance_ids = list(resource_mapping.keys())
        return self._group_instances_from_mapping(instance_ids, resource_mapping)

    def _group_instances_by_asg(self, instance_ids: list[str]) -> dict[Optional[str], dict]:
        """Group ASG instances using shared mixin logic."""
        return self._group_instances_direct(instance_ids)

    # FleetGroupingMixin hooks
    def _classify_mapping_entry(
        self, resource_id: Optional[str], desired_capacity: int
    ) -> tuple[str, Optional[str]]:
        # If we know the ASG name (resource_id), treat it as an ASG group even if
        # desired_capacity isn't populated. Missing desired_capacity should not
        # downgrade the instance to non-group handling, otherwise we skip ASG
        # capacity updates and replacements continue.
        if resource_id:
            return "group", resource_id
        if resource_id is None and desired_capacity == 0:
            return "non_group", None
        return "unknown", None

    def _collect_groups_from_instances(
        self,
        instance_ids: list[str],
        groups: dict[Optional[str], dict],
        group_ids_to_fetch: set[str],
    ) -> None:
        """Populate ASG groups using describe_auto_scaling_instances."""
        if not instance_ids:
            return

        try:
            for chunk in self._chunk_list(instance_ids, self.grouping_chunk_size):
                try:
                    response = self._retry_with_backoff(
                        self.aws_client.autoscaling_client.describe_auto_scaling_instances,
                        operation_type="read_only",
                        InstanceIds=chunk,
                    )

                    asg_instance_ids = set()

                    for entry in response.get("AutoScalingInstances", []):
                        instance_id = entry.get("InstanceId")
                        asg_name = entry.get("AutoScalingGroupName")

                        if instance_id and asg_name:
                            self._add_instance_to_group(groups, asg_name, instance_id)
                            asg_instance_ids.add(instance_id)
                            group_ids_to_fetch.add(asg_name)

                    missing_instances = [iid for iid in chunk if iid not in asg_instance_ids]
                    for iid in missing_instances:
                        self._add_non_group_instance(groups, iid)

                except Exception as exc:
                    self._logger.warning(
                        "Failed to describe ASG instances for chunk %s: %s", chunk, exc
                    )
                    for iid in chunk:
                        self._add_non_group_instance(groups, iid)

        except Exception as exc:
            self._logger.error("Failed to group instances by ASG: %s", exc)
            groups.clear()
            group_ids_to_fetch.clear()
            groups[None] = {"instance_ids": instance_ids.copy()}

    def _fetch_and_attach_group_details(
        self, group_ids: set[str], groups: dict[Optional[str], dict]
    ) -> None:
        """Fetch ASG configuration details for grouped ASGs."""
        if not group_ids:
            return

        try:
            asg_names_list = list(group_ids)
            for asg_chunk in self._chunk_list(asg_names_list, self.grouping_chunk_size):
                asg_response = self._retry_with_backoff(
                    self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                    operation_type="read_only",
                    AutoScalingGroupNames=asg_chunk,
                )

                for asg_details in asg_response.get("AutoScalingGroups", []):
                    asg_name = asg_details.get("AutoScalingGroupName")
                    if asg_name in groups:
                        groups[asg_name]["asg_details"] = {
                            "AutoScalingGroupName": asg_name,
                            "DesiredCapacity": asg_details.get("DesiredCapacity", 0),
                            "MinSize": asg_details.get("MinSize", 0),
                            "MaxSize": asg_details.get("MaxSize", 0),
                        }

        except Exception as exc:
            self._logger.warning("Failed to fetch ASG details: %s", exc)

    def _group_details_key(self) -> str:
        return "asg_details"

    def _grouping_label(self) -> str:
        return "ASG"

    @staticmethod
    def _chunk_list(items: list[str], chunk_size: int):
        """Yield successive chunk-sized lists from items."""
        for index in range(0, len(items), chunk_size):
            yield items[index : index + chunk_size]

    def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
        """Check the status of instances across all ASGs in the request."""
        try:
            if not request.resource_ids:
                self._logger.info("No ASG names found in request")
                return []

            all_instances = []

            # Process all ASG names instead of just the first one
            for asg_name in request.resource_ids:
                try:
                    asg_instances = self._get_asg_instances(asg_name)
                    if asg_instances:
                        formatted_instances = self._format_instance_data(
                            asg_instances, asg_name, request
                        )
                        all_instances.extend(formatted_instances)
                except Exception as e:
                    self._logger.error("Failed to get instances for ASG %s: %s", asg_name, e)
                    continue
            return all_instances
        except Exception as e:
            self._logger.error("Unexpected error checking ASG status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check ASG status: {e!s}")
