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
from providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from providers.aws.utilities.aws_operations import AWSOperations


@injectable
class ASGHandler(AWSHandler, BaseContextMixin):
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
        asg_config = {
            "AutoScalingGroupName": asg_name,
            "LaunchTemplate": {
                "LaunchTemplateId": launch_template_id,
                "Version": launch_template_version,
            },
            "MinSize": 0,
            "MaxSize": request.requested_count * 2,  # Allow some buffer
            "DesiredCapacity": request.requested_count,
            "DefaultCooldown": 300,
            "HealthCheckType": "EC2",
            "HealthCheckGracePeriod": 300,
            "NewInstancesProtectedFromScaleIn": True,
        }

        # Add subnet configuration
        if aws_template.subnet_ids:
            asg_config["VPCZoneIdentifier"] = ",".join(aws_template.subnet_ids)

        # Add Context field if specified
        if aws_template.context:
            asg_config["Context"] = aws_template.context

        return asg_config

    @handle_infrastructure_exceptions(context="asg_termination")
    def _get_asg_instances(self, asg_name: str) -> list[dict[str, Any]]:
        """Get instances for a specific ASG."""
        # Get ASG information
        asg_list = self._retry_with_backoff(
            lambda: self._paginate(
                self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                "AutoScalingGroups",
                AutoScalingGroupNames=[asg_name],
            )
        )

        if not asg_list:
            self._logger.warning("ASG %s not found", asg_name)
            return []

        asg = asg_list[0]
        instance_ids = [instance["InstanceId"] for instance in asg.get("Instances", [])]

        if not instance_ids:
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
        self, machine_ids: list[str], resource_mapping: list[tuple[str, str, int]] = None
    ) -> None:
        """Release hosts across multiple ASGs by detecting ASG membership.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: List of tuples (instance_id, resource_id or None, desired_capacity) for intelligent resource management
        """
        try:
            if not machine_ids:
                self._logger.warning("No instance IDs provided for ASG termination")
                return

            self._logger.info("Releasing hosts for %d instances: %s", len(machine_ids), machine_ids)

            # Always use resource_mapping when available, but check each entry individually
            if resource_mapping:
                # Ensure all machine_ids are covered by adding missing instances to resource_mapping
                resource_mapping_dict = {
                    instance_id: (resource_id, desired_capacity)
                    for instance_id, resource_id, desired_capacity in resource_mapping
                }

                # Add any missing machine_ids to resource_mapping with None values (requires AWS API lookup)
                complete_resource_mapping = []
                for instance_id in machine_ids:
                    if instance_id in resource_mapping_dict:
                        resource_id, desired_capacity = resource_mapping_dict[instance_id]
                        complete_resource_mapping.append(
                            (instance_id, resource_id, desired_capacity)
                        )
                    else:
                        # Instance not in resource_mapping - add with None values to trigger AWS API lookup
                        complete_resource_mapping.append((instance_id, None, 0))
                        self._logger.debug(
                            f"Instance {instance_id} not in resource mapping, will use AWS API lookup"
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
            # Still terminate the instances even if ASG details are missing
            self.aws_ops.terminate_instances_with_fallback(
                asg_instance_ids,
                self._request_adapter,
                f"ASG {asg_name} instances (no ASG details)",
            )
            self._logger.info(
                "Terminated ASG %s instances without ASG operations: %s", asg_name, asg_instance_ids
            )
            return

        # Detach instances from ASG first
        self._retry_with_backoff(
            self.aws_client.autoscaling_client.detach_instances,
            operation_type="critical",
            AutoScalingGroupName=asg_name,
            InstanceIds=asg_instance_ids,
            ShouldDecrementDesiredCapacity=True,
        )
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
                ForceDelete=True,  # Force delete any remaining instances
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
        self, resource_mapping: list[tuple[str, str, int]]
    ) -> dict[Optional[str], dict]:
        """
        Group EC2 instances by their Auto Scaling Group (ASG) membership using resource mapping data.

        This function performs the following main steps:
        1. Separates instances into ASG and non-ASG groups based on resource mapping
        2. For instances without ASG information in mapping, queries AWS API to determine ASG membership
        3. Retrieves minimal ASG details (name and current capacity) for identified ASGs
        4. Returns organized groups with instance IDs and ASG configuration data

        Args:
            resource_mapping: List of tuples containing:
                - instance_id (str): EC2 instance identifier
                - resource_id (str or None): ASG name if instance belongs to ASG, None otherwise
                - desired_capacity (int): Target capacity for the ASG, 0 for non-ASG instances

        Returns:
            Dictionary mapping ASG names (or None for non-ASG instances) to group data:
            {
                "asg-name-1": {
                    "instance_ids": ["i-1234567890abcdef0", "i-0987654321fedcba0"],
                    "asg_details": {
                        "AutoScalingGroupName": "asg-name-1",
                        "DesiredCapacity": 2,
                        "MinSize": 1,
                        "MaxSize": 5
                    }
                },
                None: {
                    "instance_ids": ["i-abcdef1234567890", "i-fedcba0987654321"]
                }
            }

        Note:
            - ASG instances have both 'instance_ids' and 'asg_details' keys
            - Non-ASG instances are grouped under None key with only 'instance_ids'
            - AWS API calls are minimized by using resource mapping data when available
        """
        # Step 1: Initialize result containers and extract instance IDs from mapping
        asg_groups: dict[Optional[str], dict] = {}
        instances_without_asg_info = []
        asg_names_requiring_details = set()

        # Extract instance IDs from resource mapping and create lookup dict
        resource_map = {
            instance_id: (resource_id, desired_capacity)
            for instance_id, resource_id, desired_capacity in resource_mapping
        }
        instance_ids = [mapping[0] for mapping in resource_mapping]
        self._logger.info(f"Processing {len(instance_ids)} instances from resource mapping")

        # Step 2: Process each instance in the resource mapping to determine ASG membership
        for instance_id, resource_id, desired_capacity in resource_mapping:
            # Check if instance belongs to an ASG based on mapping data
            if resource_id and desired_capacity > 0:
                # Instance belongs to an ASG - add to ASG group
                asg_name = resource_id
                if asg_name not in asg_groups:
                    asg_groups[asg_name] = {"instance_ids": [], "asg_details": None}
                asg_groups[asg_name]["instance_ids"].append(instance_id)
                asg_names_requiring_details.add(asg_name)
                self._logger.debug(f"Instance {instance_id} assigned to ASG {asg_name}")

            elif resource_id is None and desired_capacity == 0:
                # Definitive non-ASG instance - both resource_id and desired_capacity indicate no ASG
                if None not in asg_groups:
                    asg_groups[None] = {"instance_ids": []}
                asg_groups[None]["instance_ids"].append(instance_id)
                self._logger.debug(f"Instance {instance_id} marked as non-ASG")

            else:
                # Incomplete mapping data - requires AWS API lookup
                # This includes cases like: (resource_id exists, desired_capacity=0) or (resource_id=None, desired_capacity>0)
                instances_without_asg_info.append(instance_id)
                self._logger.debug(
                    f"Instance {instance_id} requires AWS API lookup (incomplete mapping: resource_id={resource_id}, desired_capacity={desired_capacity})"
                )

        # Step 3: Query AWS API for instances with incomplete mapping information
        if instances_without_asg_info:
            self._logger.info(
                f"Querying AWS API for {len(instances_without_asg_info)} instances with incomplete mapping"
            )

            # Process instances in chunks to respect AWS API limits
            for instance_chunk in self._chunk_list(instances_without_asg_info, 50):
                try:
                    # Query AWS to determine ASG membership for this chunk
                    response = self._retry_with_backoff(
                        self.aws_client.autoscaling_client.describe_auto_scaling_instances,
                        operation_type="read_only",
                        InstanceIds=instance_chunk,
                    )

                    # Track which instances are found in ASGs
                    instances_found_in_asgs = set()

                    # Process each instance found in an ASG
                    for asg_instance_data in response.get("AutoScalingInstances", []):
                        instance_id = asg_instance_data.get("InstanceId")
                        asg_name = asg_instance_data.get("AutoScalingGroupName")

                        if instance_id and asg_name:
                            # Add instance to appropriate ASG group
                            if asg_name not in asg_groups:
                                asg_groups[asg_name] = {"instance_ids": [], "asg_details": None}
                            asg_groups[asg_name]["instance_ids"].append(instance_id)
                            instances_found_in_asgs.add(instance_id)
                            asg_names_requiring_details.add(asg_name)

                    # Add remaining instances to non-ASG group
                    non_asg_instances = [
                        iid for iid in instance_chunk if iid not in instances_found_in_asgs
                    ]
                    if non_asg_instances:
                        if None not in asg_groups:
                            asg_groups[None] = {"instance_ids": []}
                        asg_groups[None]["instance_ids"].extend(non_asg_instances)

                except Exception as api_error:
                    self._logger.warning(f"AWS API call failed for instance chunk: {api_error}")
                    # Fallback: treat all instances in failed chunk as non-ASG
                    if None not in asg_groups:
                        asg_groups[None] = {"instance_ids": []}
                    asg_groups[None]["instance_ids"].extend(instance_chunk)

        # Step 4: Retrieve essential ASG details (name and current capacity) for identified ASGs
        if asg_names_requiring_details:
            self._logger.info(f"Retrieving details for {len(asg_names_requiring_details)} ASGs")

            # Process ASG names in chunks to respect AWS API limits
            for asg_name_chunk in self._chunk_list(list(asg_names_requiring_details), 50):
                try:
                    # Query AWS for ASG configuration details
                    asg_response = self._retry_with_backoff(
                        self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                        operation_type="read_only",
                        AutoScalingGroupNames=asg_name_chunk,
                    )

                    # Extract only essential ASG information (name and capacity)
                    for full_asg_config in asg_response.get("AutoScalingGroups", []):
                        asg_name = full_asg_config.get("AutoScalingGroupName")
                        if asg_name in asg_groups:
                            # Store only essential ASG details to minimize memory usage
                            essential_asg_details = {
                                "AutoScalingGroupName": asg_name,
                                "DesiredCapacity": full_asg_config.get("DesiredCapacity", 0),
                                "MinSize": full_asg_config.get("MinSize", 0),
                                "MaxSize": full_asg_config.get("MaxSize", 0),
                            }
                            asg_groups[asg_name]["asg_details"] = essential_asg_details

                except Exception as asg_details_error:
                    self._logger.warning(f"Failed to retrieve ASG details: {asg_details_error}")
                    # Continue without ASG details - calling methods will handle missing details gracefully

        # Log summary of grouping results
        total_instances = len(instance_ids)
        api_lookups_performed = len(instances_without_asg_info)
        api_calls_avoided = total_instances - api_lookups_performed

        self._logger.info(f"Grouped {total_instances} instances into {len(asg_groups)} groups")
        self._logger.info(
            f"Avoided AWS API calls for {api_calls_avoided} instances using resource mapping"
        )

        return asg_groups

    def _group_instances_by_asg(self, instance_ids: list[str]) -> dict[Optional[str], dict]:
        """
        Group instances by their ASG membership and return full ASG details.

        Args:
            instance_ids: List of EC2 instance IDs

        Returns:
            Dictionary mapping ASG names to ASG details dict containing:
            - 'instance_ids': list of instance IDs
            - 'asg_details': full ASG configuration (for ASG instances only)
            Non-ASG instances are grouped under None key with only 'instance_ids'.
        """
        asg_groups: dict[Optional[str], dict] = {}
        asg_names_to_fetch = set()

        try:
            # First, group instances by ASG name
            for chunk in self._chunk_list(instance_ids, 50):
                try:
                    response = self._retry_with_backoff(
                        self.aws_client.autoscaling_client.describe_auto_scaling_instances,
                        operation_type="read_only",
                        InstanceIds=chunk,
                    )

                    # Track which instances were found in ASGs
                    asg_instance_ids = set()

                    # Group instances by ASG
                    for entry in response.get("AutoScalingInstances", []):
                        instance_id = entry.get("InstanceId")
                        asg_name = entry.get("AutoScalingGroupName")

                        if instance_id and asg_name:
                            if asg_name not in asg_groups:
                                asg_groups[asg_name] = {"instance_ids": [], "asg_details": None}
                            asg_groups[asg_name]["instance_ids"].append(instance_id)
                            asg_instance_ids.add(instance_id)
                            asg_names_to_fetch.add(asg_name)

                    # Add non-ASG instances to None group
                    non_asg_instances = [iid for iid in chunk if iid not in asg_instance_ids]
                    if non_asg_instances:
                        if None not in asg_groups:
                            asg_groups[None] = {"instance_ids": []}
                        asg_groups[None]["instance_ids"].extend(non_asg_instances)

                except Exception as e:
                    self._logger.warning(f"Failed to describe ASG instances for chunk {chunk}: {e}")
                    # Add all instances in this chunk to non-ASG group as fallback
                    if None not in asg_groups:
                        asg_groups[None] = {"instance_ids": []}
                    asg_groups[None]["instance_ids"].extend(chunk)

            # Now fetch ASG details for all identified ASGs
            if asg_names_to_fetch:
                try:
                    asg_names_list = list(asg_names_to_fetch)
                    for asg_chunk in self._chunk_list(asg_names_list, 50):
                        asg_response = self._retry_with_backoff(
                            self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                            operation_type="read_only",
                            AutoScalingGroupNames=asg_chunk,
                        )

                        for asg_details in asg_response.get("AutoScalingGroups", []):
                            asg_name = asg_details.get("AutoScalingGroupName")
                            if asg_name in asg_groups:
                                asg_groups[asg_name]["asg_details"] = asg_details

                except Exception as e:
                    self._logger.warning(f"Failed to fetch ASG details: {e}")
                    # Continue without ASG details - methods will handle missing details

        except Exception as e:
            self._logger.error(f"Failed to group instances by ASG: {e}")
            # Fallback: treat all instances as non-ASG
            asg_groups = {None: {"instance_ids": instance_ids.copy()}}

        self._logger.debug(f"Grouped {len(instance_ids)} instances into {len(asg_groups)} groups")
        return asg_groups

    @staticmethod
    def _chunk_list(items: list[str], chunk_size: int):
        """Yield successive chunk-sized lists from items."""
        for index in range(0, len(items), chunk_size):
            yield items[index : index + chunk_size]

    async def check_hosts_status(self, request: Request) -> list[dict[str, Any]]:
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
