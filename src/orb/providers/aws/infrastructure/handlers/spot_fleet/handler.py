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

from typing import Any, Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSInfrastructureError,
    AWSValidationError,
)
from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.shared.base_context_mixin import BaseContextMixin
from orb.providers.aws.infrastructure.handlers.shared.fleet_grouping_mixin import FleetGroupingMixin
from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
    SpotFleetConfigBuilder,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.release_manager import (
    SpotFleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.validator import SpotFleetValidator
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations


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
        config_port: Optional[ConfigurationPort] = None,
        spot_fleet_validator: Optional[SpotFleetValidator] = None,
        config_builder: Optional[SpotFleetConfigBuilder] = None,
        release_manager: Optional[SpotFleetReleaseManager] = None,
    ) -> None:
        """
        Initialize the Spot Fleet handler.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            aws_ops: AWS operations utility
            launch_template_manager: Launch template manager for AWS-specific operations
            request_adapter: Optional request adapter for terminating instances
            machine_adapter: Optional machine adapter for instance mapping
            aws_native_spec_service: Optional native spec service
            config_port: Optional configuration port
            spot_fleet_validator: Optional validator; constructed from aws_client/logger if not provided
            config_builder: Optional config builder; constructed from dependencies if not provided
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
        self._spot_fleet_validator = spot_fleet_validator or SpotFleetValidator(
            aws_client, logger, aws_ops
        )
        self._config_builder = config_builder or SpotFleetConfigBuilder(
            aws_native_spec_service, config_port, logger
        )
        self._release_manager = release_manager or SpotFleetReleaseManager(
            aws_client, aws_ops, request_adapter, self._cleanup_on_zero_capacity, logger
        )

    @handle_infrastructure_exceptions(context="spot_fleet_creation")
    def _acquire_hosts_internal(
        self, request: Request, aws_template: AWSTemplate
    ) -> dict[str, Any]:
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
                "provider_data": {"resource_type": "spot_fleet", "fulfillment_final": True},
            }
        except Exception as e:
            self._logger.error("SpotFleet creation failed: %s", e)
            return {"success": False, "resource_ids": [], "instances": [], "error_message": str(e)}

    def _create_spot_fleet_with_response(
        self, request: Request, aws_template: AWSTemplate
    ) -> dict[str, Any]:
        """Create Spot Fleet and return full AWS response."""
        # Resolve fleet role ARNs before validation (requires STS, lives on handler)
        aws_template = self._resolve_fleet_role(aws_template)

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
        fleet_config = self._config_builder.build(
            template=aws_template,
            request=request,
            lt_id=launch_template_result.template_id,
            lt_version=launch_template_result.version,
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
        # Delegate Spot Fleet specific validation to the validator
        self._spot_fleet_validator.validate(aws_template)

    def _resolve_fleet_role(self, aws_template: AWSTemplate) -> AWSTemplate:
        """Resolve short or cross-service fleet role ARNs to full SpotFleet ARNs.

        fleet_role is read from provider.config (not template_defaults) because it is
        account-scoped — a single IAM role applies to all fleets in the account, so it
        belongs alongside other account-scoped properties (profile, region) rather than
        in per-template defaults.

        Requires STS access, so lives on the handler rather than the builder.
        Returns the template unchanged if no resolution is needed.
        """
        fleet_role = aws_template.fleet_role
        if not fleet_role and self.config_port is not None:
            provider_config = self.config_port.get_provider_config()
            if provider_config is not None:
                active_override = self.config_port.get_active_provider_override()
                providers = getattr(provider_config, "providers", [])
                for p in providers:
                    if active_override is None or p.name == active_override:
                        fleet_role = (p.config or {}).get("fleet_role")
                        if fleet_role:
                            break
        if not fleet_role:
            return aws_template

        resolved: Optional[str] = None
        if "ec2fleet.amazonaws.com/AWSServiceRoleForEC2Fleet" in fleet_role:
            account_id = self.aws_client.sts_client.get_caller_identity()["Account"]
            resolved = (
                f"arn:aws:iam::{account_id}:role/aws-service-role/"
                f"spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
            )
            self._logger.info("Converted EC2Fleet role to SpotFleet role: %s", resolved)
        elif fleet_role == "AWSServiceRoleForEC2SpotFleet":
            account_id = self.aws_client.sts_client.get_caller_identity()["Account"]
            resolved = (
                f"arn:aws:iam::{account_id}:role/aws-service-role/"
                f"spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet"
            )
        else:
            resolved = fleet_role

        if resolved != aws_template.fleet_role:
            aws_template = aws_template.model_copy(update={"fleet_role": resolved})
        return aws_template

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
                            fleet_instances, fleet_id, self._resolve_provider_api(request)
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

    def _resolve_provider_api(
        self, request: Request, aws_template: Optional[AWSTemplate] = None
    ) -> str:
        """Resolve the provider_api value to stamp onto instance data."""
        metadata = getattr(request, "metadata", {}) or {}
        return metadata.get("provider_api", "SpotFleet")

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
        request_id: str = "",
    ) -> None:
        """Release hosts across multiple Spot Fleets by detecting fleet membership.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: Dict mapping instance_id to (resource_id or None, desired_capacity)
            request_id: Original provisioning request ID (unused by SpotFleet handler — recovered from fleet tag)
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
        """Find the Spot Fleet request ID for a specific instance."""
        return self._release_manager.find_fleet_for_instance(instance_id)

    def cancel_resource(self, resource_id: str, request_id: str) -> dict[str, Any]:
        """Cancel a Spot Fleet request by cancelling it and terminating its instances.

        Args:
            resource_id: The Spot Fleet request ID to cancel.
            request_id: The ORB request ID, used for launch template cleanup.

        Returns:
            Dictionary with ``status`` of ``"success"`` or ``"error"``.
        """
        try:
            self._release_manager.release(resource_id, [], {}, request_id=request_id)
            return {"status": "success", "message": f"Spot Fleet {resource_id} cancelled"}
        except Exception as e:
            self._logger.error("Failed to cancel Spot Fleet %s: %s", resource_id, e)
            return {
                "status": "error",
                "message": f"Failed to cancel Spot Fleet {resource_id}: {e!s}",
            }

    def _release_hosts_for_single_spot_fleet(
        self, fleet_id: str, fleet_instance_ids: list[str], fleet_details: dict
    ) -> None:
        """Release hosts for a single Spot Fleet."""
        request_id = fleet_details.get("request_id", "") if isinstance(fleet_details, dict) else ""
        self._release_manager.release(fleet_id, fleet_instance_ids, fleet_details, request_id=request_id)

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
                max_instances=20,
                price_type="spot",
                allocation_strategy="lowestPrice",
                fleet_type="request",
                max_price=0.05,
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "dev"},
            ),
            AWSTemplate(
                template_id="SpotFleet-Request-Diversified",
                name="Spot Fleet Request - Diversified",
                description="Spot Fleet request with diversified allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                max_instances=25,
                price_type="spot",
                allocation_strategy="diversified",
                fleet_type="request",
                max_price=0.06,
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "dev"},
            ),
            AWSTemplate(
                template_id="SpotFleet-Request-CapacityOptimized",
                name="Spot Fleet Request - Capacity Optimized",
                description="Spot Fleet request with capacity optimized allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                max_instances=30,
                price_type="spot",
                allocation_strategy="capacityOptimized",
                fleet_type="request",
                max_price=0.07,
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "dev"},
            ),
            # Maintain fleet type examples
            AWSTemplate(
                template_id="SpotFleet-Maintain-LowestPrice",
                name="Spot Fleet Maintain - Lowest Price",
                description="Spot Fleet maintain with lowest price allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                max_instances=15,
                price_type="spot",
                allocation_strategy="lowestPrice",
                fleet_type="maintain",
                max_price=0.04,
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "prod"},
            ),
            AWSTemplate(
                template_id="SpotFleet-Maintain-Diversified",
                name="Spot Fleet Maintain - Diversified",
                description="Spot Fleet maintain with diversified allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                max_instances=20,
                price_type="spot",
                allocation_strategy="diversified",
                fleet_type="maintain",
                max_price=0.05,
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "prod"},
            ),
            AWSTemplate(
                template_id="SpotFleet-Maintain-CapacityOptimized",
                name="Spot Fleet Maintain - Capacity Optimized",
                description="Spot Fleet maintain with capacity optimized allocation",
                provider_api="SpotFleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                max_instances=25,
                price_type="spot",
                allocation_strategy="capacityOptimized",
                fleet_type="maintain",
                max_price=0.06,
                subnet_ids=[],
                security_group_ids=[],
                tags={"Environment": "prod"},
            ),
        ]
