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

import logging
from typing import Any, Optional

from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from domain.template.template_aggregate import Template
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.handlers.asg.capacity_manager import ASGCapacityManager
from providers.aws.infrastructure.handlers.asg.config_builder import ASGConfigBuilder
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.infrastructure.handlers.shared.base_context_mixin import BaseContextMixin
from providers.aws.infrastructure.handlers.shared.fleet_grouping_mixin import FleetGroupingMixin
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
        request_adapter: Optional[RequestAdapterPort] = None,
        machine_adapter: Optional[AWSMachineAdapter] = None,
        aws_native_spec_service=None,
        config_port=None,
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
            aws_native_spec_service=aws_native_spec_service,
            config_port=config_port,
        )
        self._config_builder = ASGConfigBuilder(aws_native_spec_service, config_port, logger)
        self._capacity_manager = ASGCapacityManager(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=request_adapter,
            cleanup_on_zero_capacity_fn=self._cleanup_asg_on_zero_capacity,
            logger=logger,
            retry_with_backoff=self._retry_with_backoff,
            chunk_list=self._chunk_list,
        )
        self._capacity_manager.set_delete_asg_fn(lambda name: self._delete_asg(name))

    def _delete_asg(self, asg_name: str) -> None:
        """Delete an Auto Scaling Group when it's no longer needed."""
        try:
            self._logger.info("Deleting ASG %s", asg_name)
            self._retry_with_backoff(
                self.aws_client.autoscaling_client.delete_auto_scaling_group,
                operation_type="critical",
                AutoScalingGroupName=asg_name,
                ForceDelete=True,
            )
            self._logger.info("Successfully deleted ASG %s", asg_name)
        except Exception as e:
            self._logger.warning("Failed to delete ASG %s: %s", asg_name, e)

    def _cleanup_asg_on_zero_capacity(self, resource_type: str, asg_name: str) -> None:
        """Strip the ASG name prefix to recover the request ID, then delegate to base cleanup."""
        if self.config_port is not None:
            prefix = self.config_port.get_resource_prefix("asg")
            request_id = (
                asg_name[len(prefix) :] if prefix and asg_name.startswith(prefix) else asg_name
            )
        else:
            request_id = asg_name
        self._cleanup_on_zero_capacity(resource_type, request_id)

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
                "provider_data": {"resource_type": "asg", "fulfillment_final": True},
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

        # Generate ASG name
        assert self.config_port is not None, "config_port must be injected"
        asg_name = f"{self.config_port.get_resource_prefix('asg')}{request.request_id}"

        # Create ASG configuration
        asg_config = self._config_builder.build(
            asg_name=asg_name,
            template=aws_template,
            request=request,
            lt_id=launch_template_result.template_id,
            lt_version=launch_template_result.version,
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
        if getattr(aws_template, "instance_protection", None):
            self._enable_instance_protection(asg_name)  # type: ignore[attr-defined]

        # Set instance lifecycle hooks if needed
        lifecycle_hooks = getattr(aws_template, "lifecycle_hooks", None)
        if lifecycle_hooks:
            self._set_lifecycle_hooks(asg_name, lifecycle_hooks)  # type: ignore[attr-defined]

        return asg_name

    def _tag_asg(self, asg_name: str, aws_template: AWSTemplate, request_id: str) -> None:
        """Add tags to the Auto Scaling Group."""
        try:
            flat_tags = self._build_resource_tags(
                request_id=request_id,
                template=aws_template,
                resource_prefix_key="asg",
                provider_api="ASG",
            )

            # ASG create_or_update_tags requires ResourceId, ResourceType, PropagateAtLaunch
            asg_tags = [
                {
                    "Key": t["Key"],
                    "Value": t["Value"],
                    "PropagateAtLaunch": True,
                    "ResourceId": asg_name,
                    "ResourceType": "auto-scaling-group",
                }
                for t in flat_tags
            ]

            self._retry_with_backoff(
                self.aws_client.autoscaling_client.create_or_update_tags,
                operation_type="critical",
                Tags=asg_tags,
            )

            self._logger.info("Successfully tagged ASG %s", asg_name)
        except Exception as e:
            self._logger.warning("Failed to tag ASG %s: %s", asg_name, e)

    @handle_infrastructure_exceptions(context="asg_termination")
    def _get_asg_instances(
        self,
        asg_name: str,
        request_id: Optional[str] = None,
        resource_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Get instances for a specific ASG."""
        response = self._retry_with_backoff(
            self.aws_client.autoscaling_client.describe_auto_scaling_groups,
            operation_type="standard",
            AutoScalingGroupNames=[asg_name],
        )
        groups = response.get("AutoScalingGroups", [])
        if not groups:
            self._logger.warning("ASG %s not found", asg_name)
            return []
        instance_ids = [
            inst["InstanceId"] for inst in groups[0].get("Instances", []) if inst.get("InstanceId")
        ]
        if not instance_ids:
            self._logger.warning("No instances found in ASG %s", asg_name)
            return []
        return self._get_instance_details(
            instance_ids,
            request_id=request_id,
            resource_id=resource_id or asg_name,
        )

    def _resolve_provider_api(
        self, request: Request, aws_template: Optional[AWSTemplate] = None
    ) -> str:
        """Resolve the provider_api value to stamp onto instance data."""
        metadata = getattr(request, "metadata", {}) or {}
        return metadata.get("provider_api", "ASG")

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

                except (ClientError, KeyError, ValueError) as e:
                    # Skip failed chunks and log the issue for debugging
                    logging.warning("Failed to process instance chunk: %s", e)
                    continue

            return asg_mapping

        except Exception:
            return {}

    def reduce_capacity_for_instance_ids(self, instance_ids: list[str]) -> None:
        """Reduce ASG capacity ahead of instance termination to avoid replacements."""
        self._capacity_manager.reduce_capacity(instance_ids)

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
        self._capacity_manager.release_instances(asg_name, asg_instance_ids, asg_details)

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
                    asg_instances = self._get_asg_instances(
                        asg_name,
                        request_id=str(request.request_id),
                        resource_id=asg_name,
                    )
                    if asg_instances:
                        formatted_instances = self._format_instance_data(
                            asg_instances, asg_name, self._resolve_provider_api(request)
                        )
                        all_instances.extend(formatted_instances)
                except Exception as e:
                    self._logger.error("Failed to get instances for ASG %s: %s", asg_name, e)
                    continue
            return all_instances
        except Exception as e:
            self._logger.error("Unexpected error checking ASG status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check ASG status: {e!s}")

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Get example templates for ASG handler."""
        return [
            AWSTemplate(
                template_id="ASG-OnDemand",
                name="Auto Scaling Group On-Demand",
                description="Auto Scaling Group with on-demand instances only",
                provider_api="ASG",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=15,
                price_type="ondemand",
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "prod", "ManagedBy": "ORB"},
            ),
            AWSTemplate(
                template_id="ASG-Spot",
                name="Auto Scaling Group Spot",
                description="Auto Scaling Group with spot instances only",
                provider_api="ASG",
                instance_type="t3.medium",
                image_id="ami-12345678",
                max_instances=20,
                price_type="spot",
                max_price=0.05,
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "dev", "ManagedBy": "ORB"},
            ),
            AWSTemplate(
                template_id="ASG-Mixed",
                name="Auto Scaling Group Mixed",
                description="Auto Scaling Group with mixed on-demand and spot instances",
                provider_api="ASG",
                machine_types={"t3.medium": 1, "t3.large": 2},
                image_id="ami-12345678",
                max_instances=25,
                price_type="heterogeneous",
                percent_on_demand=30,
                allocation_strategy="lowestPrice",
                subnet_ids=["subnet-12345678", "subnet-87654321"],
                security_group_ids=["sg-12345678"],
                tags={"Environment": "prod", "ManagedBy": "ORB"},
            ),
        ]
