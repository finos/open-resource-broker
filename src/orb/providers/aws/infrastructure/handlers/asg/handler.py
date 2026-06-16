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

from botocore.exceptions import ClientError

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.infrastructure.logging.logger import get_logger
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSConfigurationError,
    AWSInfrastructureError,
)
from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.asg.capacity_manager import ASGCapacityManager
from orb.providers.aws.infrastructure.handlers.asg.config_builder import ASGConfigBuilder
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.shared.base_context_mixin import BaseContextMixin
from orb.providers.aws.infrastructure.handlers.shared.fleet_grouping_mixin import FleetGroupingMixin
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from orb.providers.aws.utilities.aws_operations import AWSOperations


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
        config_port: Optional[ConfigurationPort] = None,
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
    def _acquire_hosts_internal(
        self, request: Request, aws_template: AWSTemplate
    ) -> dict[str, Any]:
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

    def _create_asg_internal(self, request: Request, aws_template: AWSTemplate) -> str:
        """Create ASG with pure business logic."""
        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
        )

        # Generate ASG name
        if self.config_port is None:
            raise AWSConfigurationError("config_port must be injected before calling _create_asg")
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
            provider_api="ASG",
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
                    get_logger(__name__).warning("Failed to process instance chunk: %s", e)
                    continue

            return asg_mapping

        except Exception as e:
            get_logger(__name__).warning(
                "Failed to detect ASG membership for instances, skipping capacity reduction: %s", e
            )
            return {}

    def reduce_capacity_for_instance_ids(self, instance_ids: list[str]) -> None:
        """Reduce ASG capacity ahead of instance termination to avoid replacements."""
        self._capacity_manager.reduce_capacity(instance_ids)

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
        request_id: str = "",
    ) -> None:
        """Release hosts across multiple ASGs by detecting ASG membership.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: Dict mapping instance_id to (resource_id or None, desired_capacity) for intelligent resource management
            request_id: Original provisioning request ID (unused by ASG handler — recovered from ASG name)
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

    def cancel_resource(self, resource_id: str, request_id: str) -> dict[str, Any]:
        """Cancel an Auto Scaling Group by deleting it.

        Args:
            resource_id: The ASG name to cancel.
            request_id: The ORB request ID, used for launch template cleanup.

        Returns:
            Dictionary with ``status`` of ``"success"`` or ``"error"``.
        """
        try:
            self._delete_asg(resource_id)
            if request_id:
                self._cleanup_on_zero_capacity("asg", request_id)
            return {"status": "success", "message": f"Auto Scaling Group {resource_id} deleted"}
        except Exception as e:
            self._logger.error("Failed to cancel ASG %s: %s", resource_id, e)
            raise AWSInfrastructureError(f"Failed to cancel ASG {resource_id}: {e!s}") from e

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

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Check the status of instances across all ASGs in the request.

        Fulfilment semantics:
            sum(WeightedCapacity for InService instances) >= DesiredCapacity
            AND pending_count == 0 AND failed_count == 0 → fulfilled.

        For ASGs without WeightedCapacity (uniform instance types) each
        InService instance contributes 1 unit.
        """
        try:
            if not request.resource_ids:
                self._logger.info("No ASG names found in request")
                return CheckHostsStatusResult(
                    instances=[],
                    fulfilment=ProviderFulfilment(
                        state="in_progress",
                        message="No ASG names yet — waiting for provisioning",
                        target_units=request.requested_count,
                        running_count=0,
                        pending_count=0,
                        failed_count=0,
                    ),
                )

            all_instances: list[dict[str, Any]] = []
            asg_results: list[CheckHostsStatusResult] = []

            for asg_name in request.resource_ids:
                try:
                    result = self._get_asg_status(
                        asg_name,
                        request_id=str(request.request_id),
                        requested_count=request.requested_count,
                    )
                    all_instances.extend(result.instances)
                    asg_results.append(result)
                except Exception as e:
                    self._logger.error("Failed to get instances for ASG %s: %s", asg_name, e)
                    continue

            if not asg_results:
                return CheckHostsStatusResult(
                    instances=[],
                    fulfilment=ProviderFulfilment(
                        state="in_progress",
                        message="No ASG status available — will retry",
                    ),
                )

            if len(asg_results) == 1:
                return CheckHostsStatusResult(
                    instances=all_instances,
                    fulfilment=asg_results[0].fulfilment,
                )

            states = [r.fulfilment.state for r in asg_results]
            if all(s == "fulfilled" for s in states):
                combined_state = "fulfilled"
                combined_msg = f"All {len(asg_results)} ASGs fulfilled"
            elif any(s == "failed" for s in states):
                combined_state = "failed"
                combined_msg = "One or more ASGs failed"
            elif any(s == "partial" for s in states):
                combined_state = "partial"
                combined_msg = "One or more ASGs partially fulfilled"
            else:
                combined_state = "in_progress"
                combined_msg = "Waiting for ASG(s) to fulfil"

            return CheckHostsStatusResult(
                instances=all_instances,
                fulfilment=ProviderFulfilment(state=combined_state, message=combined_msg),
            )

        except Exception as e:
            self._logger.error("Unexpected error checking ASG status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check ASG status: {e!s}")

    def _get_asg_status(
        self,
        asg_name: str,
        request_id: str = "",
        requested_count: int = 1,
    ) -> CheckHostsStatusResult:
        """Get status + fulfilment for a specific ASG."""
        response = self._retry_with_backoff(
            self.aws_client.autoscaling_client.describe_auto_scaling_groups,
            operation_type="standard",
            AutoScalingGroupNames=[asg_name],
        )
        groups = response.get("AutoScalingGroups", [])
        if not groups:
            self._logger.warning("ASG %s not found", asg_name)
            return CheckHostsStatusResult(
                instances=[],
                fulfilment=ProviderFulfilment(
                    state="in_progress",
                    message=f"ASG {asg_name} not yet visible — waiting",
                    target_units=requested_count,
                    running_count=0,
                    pending_count=0,
                    failed_count=0,
                ),
            )

        group = groups[0]
        desired_capacity: int = group.get("DesiredCapacity") or 0
        raw_instances = group.get("Instances") or []

        # Compute weighted fulfilment from ASG Instances list
        in_service_weighted = sum(
            int(inst.get("WeightedCapacity") or 1)
            for inst in raw_instances
            if inst.get("LifecycleState") == "InService"
        )
        pending_raw = [
            inst for inst in raw_instances if inst.get("LifecycleState") in ("Pending", "Pending:Wait", "Pending:Proceed")
        ]
        pending_count = len(pending_raw)
        in_service_count = sum(
            1 for inst in raw_instances if inst.get("LifecycleState") == "InService"
        )

        # Get full EC2 instance details for instances in the ASG
        instance_ids = [
            inst["InstanceId"]
            for inst in raw_instances
            if inst.get("InstanceId") and inst.get("LifecycleState") not in ("Terminating", "Terminated", "Detaching", "Detached")
        ]

        if not instance_ids:
            return CheckHostsStatusResult(
                instances=[],
                fulfilment=self._compute_asg_fulfilment(
                    desired_capacity=desired_capacity,
                    in_service_weighted=in_service_weighted,
                    pending_count=pending_count,
                    in_service_count=in_service_count,
                    ec2_instances=[],
                ),
            )

        try:
            instance_details = self._get_instance_details(
                instance_ids,
                request_id=request_id,
                resource_id=asg_name,
                provider_api="ASG",
            )
        except Exception as e:
            self._logger.warning("Failed to describe EC2 instances for ASG %s: %s", asg_name, e)
            instance_details = []

        provider_api_value = (getattr(self, "metadata", {}) or {}).get("provider_api", "ASG")
        formatted = self._format_instance_data(
            instance_details, asg_name, provider_api_value
        ) if instance_details else []

        fulfilment = self._compute_asg_fulfilment(
            desired_capacity=desired_capacity,
            in_service_weighted=in_service_weighted,
            pending_count=pending_count,
            in_service_count=in_service_count,
            ec2_instances=formatted,
        )
        return CheckHostsStatusResult(instances=formatted, fulfilment=fulfilment)

    def _compute_asg_fulfilment(
        self,
        desired_capacity: int,
        in_service_weighted: int,
        pending_count: int,
        in_service_count: int,
        ec2_instances: list[dict[str, Any]],
    ) -> ProviderFulfilment:
        """Compute ProviderFulfilment for an ASG request.

        sum(WeightedCapacity for InService) >= DesiredCapacity AND
        pending_count == 0 AND failed_count == 0 → fulfilled.
        """
        failed_count = sum(
            1 for i in ec2_instances if i.get("status") in ("failed", "error")
        )
        running_count = sum(1 for i in ec2_instances if i.get("status") == "running")

        asg_fully_fulfilled = (
            desired_capacity > 0 and in_service_weighted >= desired_capacity
        )

        if asg_fully_fulfilled and pending_count == 0 and failed_count == 0:
            return ProviderFulfilment(
                state="fulfilled",
                message=(
                    f"ASG fulfilled: {in_service_count} instance(s) InService "
                    f"({in_service_weighted}/{desired_capacity} weighted capacity)"
                ),
                target_units=desired_capacity,
                fulfilled_units=in_service_weighted,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        elif failed_count > 0 and in_service_count == 0 and pending_count == 0:
            return ProviderFulfilment(
                state="failed",
                message=f"ASG failed: {failed_count} instance(s) in failure state",
                target_units=desired_capacity,
                fulfilled_units=in_service_weighted,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        else:
            return ProviderFulfilment(
                state="in_progress",
                message=(
                    f"ASG: {in_service_count} InService, {pending_count} pending "
                    f"({in_service_weighted}/{desired_capacity} weighted capacity)"
                ),
                target_units=desired_capacity,
                fulfilled_units=in_service_weighted,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Get example templates for ASG handler."""
        return [
            AWSTemplate(
                template_id="ASG-OnDemand",
                name="Auto Scaling Group On-Demand",
                description="Auto Scaling Group with on-demand instances only",
                provider_api="ASG",
                machine_types={"t3.medium": 2, "t3.xlarge": 4},
                max_instances=15,
                price_type="ondemand",
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "prod"},
            ),
            AWSTemplate(
                template_id="ASG-Spot",
                name="Auto Scaling Group Spot",
                description="Auto Scaling Group with spot instances only",
                provider_api="ASG",
                machine_types={"t3.medium": 2, "t3.xlarge": 4},
                max_instances=20,
                price_type="spot",
                max_price=0.10,
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "dev"},
            ),
            AWSTemplate(
                template_id="ASG-Mixed",
                name="Auto Scaling Group Mixed",
                description="Auto Scaling Group with mixed on-demand and spot instances",
                provider_api="ASG",
                machine_types={"t3.medium": 2, "t3.large": 2, "t3.xlarge": 4},
                max_instances=25,
                price_type="heterogeneous",
                percent_on_demand=30,
                allocation_strategy="lowestPrice",
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "prod"},
            ),
        ]
