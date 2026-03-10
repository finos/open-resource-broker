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

from typing import Any, Optional

from botocore.exceptions import ClientError

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.infrastructure.resilience import CircuitBreakerOpenError
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import AWSAllocationStrategy, AWSFleetType
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSEntityNotFoundError,
    AWSInfrastructureError,
    AWSValidationError,
)
from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.config_builder import EC2FleetConfigBuilder
from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
    EC2FleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.shared.base_context_mixin import BaseContextMixin
from orb.providers.aws.infrastructure.handlers.shared.fleet_grouping_mixin import FleetGroupingMixin
from orb.providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations


@injectable
class EC2FleetHandler(AWSHandler, BaseContextMixin, FleetGroupingMixin):
    """Handler for EC2 Fleet operations."""

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
        fleet_config_builder: Optional[EC2FleetConfigBuilder] = None,
        fleet_release_manager: Optional[EC2FleetReleaseManager] = None,
    ) -> None:
        """
        Initialize the EC2 Fleet handler.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            aws_ops: AWS operations utility
            launch_template_manager: Launch template manager for AWS-specific operations
            request_adapter: Optional request adapter for terminating instances
            machine_adapter: Optional machine adapter for instance mapping
            aws_native_spec_service: Optional native spec service for template rendering
            config_port: Optional configuration port
            fleet_config_builder: Optional pre-built config builder; constructed from
                aws_native_spec_service and config_port when not provided
            fleet_release_manager: Optional pre-built release manager; constructed from
                handler dependencies when not provided
        """
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
        self._fleet_config_builder = fleet_config_builder or EC2FleetConfigBuilder(
            native_spec_service=aws_native_spec_service,
            config_port=config_port,
            logger=logger,
        )
        self._fleet_release_manager = fleet_release_manager or EC2FleetReleaseManager(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=request_adapter,
            config_port=config_port,
            logger=logger,
            retry_fn=self._retry_with_backoff,
            paginate_fn=self._paginate,
            collect_with_next_token_fn=self._collect_with_next_token,
            cleanup_on_zero_capacity_fn=self._cleanup_on_zero_capacity,
        )

    @handle_infrastructure_exceptions(context="ec2_fleet_creation")
    def _acquire_hosts_internal(
        self, request: Request, aws_template: AWSTemplate
    ) -> dict[str, Any]:
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
                except ValueError as e:
                    self._logger.warning("Unknown fleet type value, skipping: %s", e)
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

            fleet_errors = fleet_result.get("metadata_updates", {}).get("fleet_errors", [])
            capacity_error_codes = {
                "InsufficientInstanceCapacity",
                "SpotMaxPriceTooLow",
                "MaxSpotInstanceCountExceeded",
            }
            capacity_constrained = any(
                e.get("error_code") in capacity_error_codes for e in fleet_errors
            )
            fleet_type_value = (
                aws_template.fleet_type.value
                if aws_template.fleet_type is not None
                else aws_template.fleet_type
            )
            return {
                "success": True,
                "resource_ids": [fleet_id],
                "instances": instances,
                "provider_data": {
                    "resource_type": "ec2_fleet",
                    "fleet_type": fleet_type_value,
                    "fleet_errors": fleet_errors,
                    "fulfillment_final": fleet_type is not AWSFleetType.INSTANT,
                    "capacity_constrained": capacity_constrained,
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
        # Validate fleet type
        if not aws_template.fleet_type:
            raise AWSValidationError("Fleet type is required for EC2Fleet")

        fleet_type = aws_template.fleet_type

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

    def _resolve_provider_api(
        self, request: Request, aws_template: Optional[AWSTemplate] = None
    ) -> str:
        """Resolve the provider_api value to stamp onto instance data."""
        if aws_template and aws_template.provider_api is not None:
            return (
                aws_template.provider_api.value
                if hasattr(aws_template.provider_api, "value")
                else str(aws_template.provider_api)
            )
        metadata = getattr(request, "metadata", {}) or {}
        return metadata.get("provider_api", "EC2Fleet")

    def _create_fleet_config(
        self,
        template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> dict[str, Any]:
        """Build the create_fleet API payload by delegating to EC2FleetConfigBuilder."""
        return self._fleet_config_builder.build(
            template=template,
            request=request,
            lt_id=launch_template_id,
            lt_version=launch_template_version,
        )

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
            return self._format_instance_data(
                instance_details, fleet_id, self._resolve_provider_api(request)
            )

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
        request_id: str = "",
    ) -> None:
        """Release hosts across multiple EC2 Fleets by detecting fleet membership.

        Args:
            machine_ids: List of instance IDs to terminate
            resource_mapping: Dict mapping instance_id to (resource_id or None, desired_capacity)
            request_id: Original provisioning request ID (unused by EC2Fleet handler — recovered from fleet tag)
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

                                # AWS deletes instant fleet records; recover request_id from instance tags for cleanup.
                                instance_tags = {
                                    t.get("Key"): t.get("Value") for t in instance.get("Tags", [])
                                }
                                if instance_tags.get("orb:fleet-type") == "instant":
                                    orb_request_id = instance_tags.get("orb:request-id", "")
                                    if orb_request_id and not groups[ec2_fleet_id].get(
                                        "request_id"
                                    ):
                                        groups[ec2_fleet_id]["request_id"] = orb_request_id

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
        """Find the EC2 Fleet ID for a specific instance by querying active fleets."""
        return self._fleet_release_manager.find_fleet_for_instance(instance_id)

    def cancel_resource(self, resource_id: str, request_id: str) -> dict[str, Any]:
        """Cancel an EC2 Fleet by deleting it and terminating its instances.

        Args:
            resource_id: The EC2 Fleet ID to cancel.
            request_id: The ORB request ID, used for launch template cleanup.

        Returns:
            Dictionary with ``status`` of ``"success"`` or ``"error"``.
        """
        try:
            self._fleet_release_manager.release(resource_id, [], {}, request_id=request_id)
            return {"status": "success", "message": f"EC2 Fleet {resource_id} cancelled"}
        except Exception as e:
            self._logger.error("Failed to cancel EC2 Fleet %s: %s", resource_id, e)
            return {
                "status": "error",
                "message": f"Failed to cancel EC2 Fleet {resource_id}: {e!s}",
            }

    def _release_hosts_for_single_ec2_fleet(
        self, fleet_id: str, fleet_instance_ids: list[str], fleet_details: dict
    ) -> None:
        """Release hosts for a single EC2 Fleet, delegating to EC2FleetReleaseManager."""
        request_id = fleet_details.get("request_id", "") if isinstance(fleet_details, dict) else ""
        self._fleet_release_manager.release(fleet_id, fleet_instance_ids, fleet_details, request_id)

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
                machine_types={"t3.medium": 1},
                max_instances=10,
                price_type="ondemand",
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "dev"},
                metadata={"fleet_type": "instant"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Instant-Spot",
                name="EC2 Fleet Instant Spot",
                description="EC2 Fleet with instant fulfillment using spot instances",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1},
                max_instances=10,
                price_type="spot",
                max_price=0.05,
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "dev"},
                metadata={"fleet_type": "instant"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Instant-Mixed",
                name="EC2 Fleet Instant Mixed",
                description="EC2 Fleet with instant fulfillment using mixed pricing",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1},
                max_instances=10,
                price_type="heterogeneous",
                percent_on_demand=30,
                allocation_strategy="diversified",
                max_price=0.05,
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "dev"},
                metadata={"fleet_type": "instant", "percent_on_demand": 30},
            ),
            # Request fleet types
            AWSTemplate(
                template_id="EC2Fleet-Request-OnDemand",
                name="EC2 Fleet Request On-Demand",
                description="EC2 Fleet with request fulfillment using on-demand instances",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1},
                max_instances=15,
                price_type="ondemand",
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "test"},
                metadata={"fleet_type": "request"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Request-Spot",
                name="EC2 Fleet Request Spot",
                description="EC2 Fleet with request fulfillment using spot instances",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1},
                max_instances=20,
                price_type="spot",
                allocation_strategy="capacityOptimized",
                max_price=0.08,
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "test"},
                metadata={"fleet_type": "request"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Request-Mixed",
                name="EC2 Fleet Request Mixed",
                description="EC2 Fleet with request fulfillment using mixed pricing",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1, "t3.large": 2},
                max_instances=25,
                price_type="heterogeneous",
                percent_on_demand=40,
                allocation_strategy="diversified",
                allocation_strategy_on_demand=AWSAllocationStrategy.from_string("lowestPrice"),
                max_price=0.08,
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "test"},
                metadata={"fleet_type": "request", "percent_on_demand": 40},
            ),
            # Maintain fleet types
            AWSTemplate(
                template_id="EC2Fleet-Maintain-OnDemand",
                name="EC2 Fleet Maintain On-Demand",
                description="EC2 Fleet with maintain capacity using on-demand instances",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1},
                max_instances=12,
                price_type="ondemand",
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "prod"},
                metadata={"fleet_type": "maintain"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Maintain-Spot",
                name="EC2 Fleet Maintain Spot",
                description="EC2 Fleet with maintain capacity using spot instances",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1},
                max_instances=30,
                price_type="spot",
                allocation_strategy="priceCapacityOptimized",
                max_price=0.10,
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "prod"},
                metadata={"fleet_type": "maintain"},
            ),
            AWSTemplate(
                template_id="EC2Fleet-Maintain-Mixed",
                name="EC2 Fleet Maintain Mixed",
                description="EC2 Fleet with maintain capacity using mixed pricing",
                provider_api="EC2Fleet",
                machine_types={"t3.medium": 1, "t3.large": 2, "t3.xlarge": 3},
                max_instances=50,
                price_type="heterogeneous",
                percent_on_demand=50,
                allocation_strategy="capacityOptimized",
                allocation_strategy_on_demand=AWSAllocationStrategy.from_string("prioritized"),
                max_price=0.12,
                subnet_ids=[],
                security_group_ids=[],
                tags={"environment": "prod"},
                metadata={"fleet_type": "maintain", "percent_on_demand": 50},
            ),
        ]
