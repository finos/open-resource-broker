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

from typing import TYPE_CHECKING, Any, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from infrastructure.utilities.common.resource_naming import get_resource_prefix
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.handlers.base_context_mixin import BaseContextMixin
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.utilities.aws_operations import AWSOperations

if TYPE_CHECKING:
    from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter


@injectable
class ASGHandler(AWSHandler, BaseContextMixin):
    """Handler for Auto Scaling Group operations."""

    def __init__(
        self,
        aws_client,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager,
        request_adapter: RequestAdapterPort = None,
        machine_adapter: Optional["AWSMachineAdapter"] = None,
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
        self._tag_asg(asg_name, aws_template, request)

        # Enable instance protection if specified
        if hasattr(aws_template, "instance_protection") and aws_template.instance_protection:
            self._enable_instance_protection(asg_name)

        # Set instance lifecycle hooks if needed
        if hasattr(aws_template, "lifecycle_hooks") and aws_template.lifecycle_hooks:
            self._set_lifecycle_hooks(asg_name, aws_template.lifecycle_hooks)

        return asg_name

    def _tag_asg(self, asg_name: str, aws_template: AWSTemplate, request: Request) -> None:
        """Add tags to the Auto Scaling Group."""
        try:
            created_by = self._get_package_name()

            # Prepare standard tags with proper ResourceId and ResourceType
            tags = [
                {
                    "Key": "Name",
                    "Value": f"hostfactory-asg-{request.request_id}",
                    "PropagateAtLaunch": True,
                    "ResourceId": asg_name,
                    "ResourceType": "auto-scaling-group",
                },
                {
                    "Key": "RequestId",
                    "Value": str(request.request_id),
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
        context = self._prepare_base_context(template, request)

        # Add capacity distribution (for consistency, even if not all used)
        context.update(self._calculate_capacity_distribution(template, request))

        # Add standard flags
        context.update(self._prepare_standard_flags(template))

        # Add standard tags
        tag_context = self._prepare_standard_tags(template, request)
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

    def release_hosts(self, request: Request) -> None:
        """Release hosts across all ASGs in the request."""
        try:
            if not request.resource_ids:
                raise AWSInfrastructureError("No ASG names found in request")

            # Handle all ASG resource IDs in the request
            for asg_name in request.resource_ids:
                # Get instance IDs from machine references
                instance_ids = []
                if request.machine_references:
                    instance_ids = [m.machine_id for m in request.machine_references]

                if instance_ids:
                    # Get ASG details first
                    asg_response = self._retry_with_backoff(
                        self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                        AutoScalingGroupNames=[asg_name],
                    )
                    if not asg_response["AutoScalingGroups"]:
                        raise AWSInfrastructureError(f"ASG {asg_name} not found")

                    asg = asg_response["AutoScalingGroups"][0]

                    # Reduce desired capacity first
                    current_capacity = asg["DesiredCapacity"]
                    new_capacity = max(0, current_capacity - len(instance_ids))

                    self._retry_with_backoff(
                        self.aws_client.autoscaling_client.update_auto_scaling_group,
                        operation_type="critical",
                        AutoScalingGroupName=asg_name,
                        DesiredCapacity=new_capacity,
                        MinSize=min(new_capacity, asg["MinSize"]),
                    )
                    self._logger.info("Reduced ASG %s capacity to %s", asg_name, new_capacity)

                    # Detach instances from ASG
                    self._retry_with_backoff(
                        self.aws_client.autoscaling_client.detach_instances,
                        operation_type="critical",
                        AutoScalingGroupName=asg_name,
                        InstanceIds=instance_ids,
                        ShouldDecrementDesiredCapacity=True,
                    )
                    self._logger.info("Detached instances from ASG: %s", instance_ids)

                    # Use consolidated AWS operations utility for instance termination
                    self.aws_ops.terminate_instances_with_fallback(
                        instance_ids, self._request_adapter, "ASG instances"
                    )
                    self._logger.info("Terminated instances: %s", instance_ids)
                else:
                    # Delete entire ASG
                    self._retry_with_backoff(
                        self.aws_client.autoscaling_client.delete_auto_scaling_group,
                        operation_type="critical",
                        AutoScalingGroupName=asg_name,
                        ForceDelete=True,
                    )
                    self._logger.info("Deleted Auto Scaling Group: %s", asg_name)
        except Exception as e:
            self._logger.error("Failed to release ASG hosts: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release ASG hosts: {e!s}")

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
