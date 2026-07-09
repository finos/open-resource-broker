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

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.infrastructure.di.injectable import injectable
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.infrastructure.resilience import CircuitBreakerOpenError
from orb.providers.aws.aws_fleet_capacity import FleetCapacityFulfilment
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import AWSFleetType
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSEntityNotFoundError,
    AWSInfrastructureError,
    AWSValidationError,
)
from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.ec2_fleet.config_builder import EC2FleetConfigBuilder
from orb.providers.aws.infrastructure.handlers.ec2_fleet.example_templates import (
    EC2_FLEET_EXAMPLE_TEMPLATES,
)
from orb.providers.aws.infrastructure.handlers.ec2_fleet.release_manager import (
    EC2FleetReleaseManager,
)
from orb.providers.aws.infrastructure.handlers.shared.base_context_mixin import BaseContextMixin
from orb.providers.aws.infrastructure.handlers.shared.fleet_fulfilment import (
    compute_ec2fleet_fulfilment,
)
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
            fleet_type = aws_template.fleet_type
            if not isinstance(fleet_type, AWSFleetType):
                try:
                    fleet_type = AWSFleetType(str(fleet_type))
                except ValueError as e:
                    self._logger.warning("Unknown fleet type value, skipping: %s", e)
                    fleet_type = None

            if fleet_type is AWSFleetType.INSTANT:
                # For instant fleets, instance IDs are already in the create_fleet response.
                # Skip describe_instances here — full details (IP, type, etc.) are resolved
                # lazily by the check_hosts_status / _check_single_fleet_status polling path.
                instance_ids = fleet_result.get("instance_ids", [])
                if instance_ids:
                    instances = [
                        {"instance_id": iid, "resource_id": fleet_id} for iid in instance_ids
                    ]
                    self._logger.info(
                        "EC2Fleet instant fleet created with %d instance(s): %s",
                        len(instance_ids),
                        instance_ids,
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
                    # ``requires_async_polling`` — True means the caller must
                    # continue polling the provider to observe further
                    # fulfillment before considering this request settled.
                    # INSTANT fleets return instance IDs synchronously but
                    # those instances are still in ``pending`` state at create
                    # time, so the create call is NOT the final answer — the
                    # polling loop must observe the running state.
                    # MAINTAIN / REQUEST fleets return only a fleet ID and no
                    # instances yet (instance arrival is purely a polling
                    # concern), so the create call IS the final synchronous
                    # answer for those types and no further polling is needed.
                    "requires_async_polling": fleet_type is AWSFleetType.INSTANT,
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

    def _default_provider_api(self) -> str:
        return "EC2Fleet"

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

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Check the status of instances in the fleet.

        Fulfilment semantics (per fleet type):
        - Instant: same as RunInstances — running_count >= requested_count
          and failed_count == 0 → fulfilled.  ``requires_async_polling=True``
          so partial/failed can be detected when pending reaches zero.
        - Maintain / Request: FulfilledCapacity >= TargetCapacity AND
          pending_count == 0 AND failed_count == 0 → fulfilled.  This is the
          weighted-fleet path that fixes the live test timeout.
        """
        self._logger.debug(f" check_hosts_status {request}")
        if not request.resource_ids:
            raise AWSInfrastructureError("No Fleet ID found in request")

        all_instances: list[dict] = []
        # For multi-fleet requests collect instances; compute combined fulfilment at the end.
        fleet_results: list[CheckHostsStatusResult] = []
        for fleet_id in request.resource_ids:
            try:
                result = self._check_single_fleet_status(fleet_id, request)
                all_instances.extend(result.instances)
                fleet_results.append(result)
            except Exception as e:
                self._logger.warning(
                    "Failed to check status for fleet %s, skipping: %s", fleet_id, e
                )

        if not fleet_results:
            return CheckHostsStatusResult(
                instances=[],
                fulfilment=ProviderFulfilment(
                    state="in_progress",
                    message="No fleet status available — will retry",
                ),
            )

        # For a single fleet (typical case) return its result directly.
        if len(fleet_results) == 1:
            return CheckHostsStatusResult(
                instances=all_instances,
                fulfilment=fleet_results[0].fulfilment,
            )

        # Multiple fleets: aggregate — all must be fulfilled for overall fulfilled.
        # Priority order matters here:
        #   1. all fulfilled              -> fulfilled
        #   2. ANY in_progress            -> in_progress  (transient — wait)
        #   3. all failed (or only fail+partial with no progress signal)
        #                                 -> failed
        #   4. any partial (no in_progress) -> partial   (terminal partial)
        #   5. fallback                   -> in_progress
        #
        # in_progress is checked BEFORE partial because we don't want a
        # request to flip to terminal-partial while another fleet is still
        # booting; that classification can only be made once every fleet
        # has reached a terminal verdict.
        states = [r.fulfilment.state for r in fleet_results]
        if all(s == "fulfilled" for s in states):
            combined_state = "fulfilled"
            combined_msg = f"All {len(fleet_results)} fleets fulfilled"
        elif any(s == "in_progress" for s in states):
            combined_state = "in_progress"
            combined_msg = "One or more fleets still provisioning"
        elif any(s == "failed" for s in states):
            combined_state = "failed"
            combined_msg = "One or more fleets failed"
        elif any(s == "partial" for s in states):
            combined_state = "partial"
            combined_msg = "One or more fleets partially fulfilled"
        else:
            combined_state = "in_progress"
            combined_msg = "Waiting for fleet(s) to fulfil"

        return CheckHostsStatusResult(
            instances=all_instances,
            fulfilment=ProviderFulfilment(state=combined_state, message=combined_msg),
        )

    def _check_single_fleet_status(self, fleet_id: str, request: Request) -> CheckHostsStatusResult:
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

            # Read capacity data from DescribeFleets (already called above — no extra API call)
            capacity = self._fetch_ec2_fleet_capacity(fleet)
            target_capacity = capacity.target_capacity_units
            fulfilled_capacity = float(capacity.fulfilled_capacity_units)

            self._logger.debug(
                "Fleet status: %s, Target capacity: %s, Fulfilled capacity: %s",
                fleet.get("FleetState"),
                target_capacity,
                fulfilled_capacity,
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

            # Per-fleet requested_count: AWS describe_fleets is the canonical
            # source. TargetCapacitySpecification.TotalTargetCapacity tells us
            # exactly how many instances this fleet was asked to provision —
            # independent of the ORB request total (which is the SUM across
            # all fleets and only meaningful for single-fleet requests).
            #
            # Without this, a request split across N fleets would have each
            # fleet's running count compared against the request total, so a
            # fully-running N-way split would look only 1/N fulfilled per
            # fleet and the aggregator would emit a wrong partial verdict.
            #
            # Fallback to request.requested_count is for the rare case where
            # AWS returns the fleet without TargetCapacitySpecification.
            per_fleet_requested = (
                int(target_capacity) if target_capacity is not None else request.requested_count
            )

            if not instance_ids:
                self._logger.info("No active instances found in fleet %s", fleet_id)
                fulfilment = self._compute_ec2fleet_fulfilment(
                    fleet_type=fleet_type,
                    instances=[],
                    target_capacity=target_capacity,
                    fulfilled_capacity=fulfilled_capacity,
                    requested_count=per_fleet_requested,
                )
                return CheckHostsStatusResult(instances=[], fulfilment=fulfilment)

            instance_details = self._get_instance_details(
                instance_ids,
                request_id=str(request.request_id),
                resource_id=fleet_id,
                provider_api="EC2Fleet",
            )
            instances = self._format_instance_data(
                instance_details, fleet_id, self._resolve_provider_api(request)
            )
            fulfilment = self._compute_ec2fleet_fulfilment(
                fleet_type=fleet_type,
                instances=instances,
                target_capacity=target_capacity,
                fulfilled_capacity=fulfilled_capacity,
                requested_count=per_fleet_requested,
            )
            return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to check EC2 Fleet status: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error checking EC2 Fleet status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check EC2 Fleet status: {e!s}")

    @staticmethod
    def _fetch_ec2_fleet_capacity(
        fleet: dict[str, Any],
        active_instance_count: int = 0,
    ) -> FleetCapacityFulfilment:
        """Extract a typed capacity snapshot from a DescribeFleets fleet entry.

        Args:
            fleet: One element from the ``DescribeFleets.Fleets`` list.
            active_instance_count: Number of instances currently observed in
                active lifecycle states.  Used as ``provisioned_instance_count``.
                For INSTANT fleets this is derived from the create-fleet
                response and is not available at describe time; callers may
                pass 0 and the field is informational only.

        Returns:
            A :class:`FleetCapacityFulfilment` with the normalised capacity
            data for this fleet.
        """
        spec = fleet.get("TargetCapacitySpecification") or {}
        target_capacity: int | None = spec.get("TotalTargetCapacity")
        fulfilled_raw: float = fleet.get("FulfilledCapacity") or 0.0
        fulfilled_units = int(fulfilled_raw)
        fulfillment_complete = target_capacity is not None and fulfilled_raw >= target_capacity
        return FleetCapacityFulfilment(
            target_capacity_units=target_capacity,
            fulfilled_capacity_units=fulfilled_units,
            provisioned_instance_count=active_instance_count,
            fulfillment_complete=fulfillment_complete,
        )

    def _compute_ec2fleet_fulfilment(
        self,
        fleet_type: Optional[AWSFleetType],
        instances: list[dict[str, Any]],
        target_capacity: Optional[int],
        fulfilled_capacity: float,
        requested_count: int,
    ) -> ProviderFulfilment:
        """Compute ProviderFulfilment for an EC2 Fleet request.

        Delegates to the module-level :func:`compute_ec2fleet_fulfilment` helper
        in ``shared/fleet_fulfilment.py``.
        """
        return compute_ec2fleet_fulfilment(
            fleet_type=fleet_type,
            instances=instances,
            target_capacity=target_capacity,
            fulfilled_capacity=fulfilled_capacity,
            requested_count=requested_count,
        )

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
        return list(EC2_FLEET_EXAMPLE_TEMPLATES)
