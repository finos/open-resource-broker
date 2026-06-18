"""SpotFleet release manager.

Encapsulates all release/teardown logic for Spot Fleet requests,
keeping SpotFleetHandler focused on orchestration.
"""

from typing import Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.fleet_release_policy import (
    compute_fleet_release_decision,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations


class SpotFleetReleaseManager:
    """Handles release and teardown of Spot Fleet resources."""

    def __init__(
        self,
        aws_client: AWSClient,
        aws_ops: AWSOperations,
        request_adapter: Optional[RequestAdapterPort],
        cleanup_on_zero_capacity_fn: Callable[[str, str], None],
        logger: LoggingPort,
        retry_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._aws_client = aws_client
        self._aws_ops = aws_ops
        self._request_adapter = request_adapter
        self._cleanup_on_zero_capacity = cleanup_on_zero_capacity_fn
        self._logger = logger
        self._retry_fn = retry_fn or getattr(aws_ops, "_retry_with_backoff", None)

    def release(
        self,
        fleet_id: str,
        instance_ids: list[str],
        fleet_details: dict[str, Any],
        request_id: str = "",
    ) -> None:
        """Release hosts for a single Spot Fleet with proper fleet management.

        For maintain-type fleets, reduces TargetCapacity before terminating
        instances to prevent AWS from replacing them. Cancels the fleet when
        capacity reaches zero and cleans up the associated launch template.

        Args:
            fleet_id: The Spot Fleet request ID.
            instance_ids: Instance IDs to terminate within this fleet.
            fleet_details: SpotFleetRequestConfig dict from describe_spot_fleet_requests,
                           or empty dict to trigger a live fetch.
        """
        self._logger.info("Processing Spot Fleet %s with %d instances", fleet_id, len(instance_ids))

        try:
            if not fleet_details:
                fleet_response = self._retry(
                    self._aws_client.ec2_client.describe_spot_fleet_requests,
                    operation_type="read_only",
                    SpotFleetRequestIds=[fleet_id],
                )
                fleet_configs = fleet_response.get("SpotFleetRequestConfigs", [])
                fleet_details = fleet_configs[0] if fleet_configs else {}

            fleet_config = fleet_details.get("SpotFleetRequestConfig", {}) if fleet_details else {}
            fleet_type = fleet_config.get("Type", "maintain")
            target_capacity = int(fleet_config.get("TargetCapacity", len(instance_ids or [])) or 0)
            on_demand_capacity = int(fleet_config.get("OnDemandTargetCapacity", 0) or 0)

            if instance_ids:
                weighted_capacity_to_return = self._sum_weighted_capacity(
                    fleet_id, fleet_config, instance_ids
                )

                decision = compute_fleet_release_decision(
                    fleet_type=fleet_type,
                    current_capacity=target_capacity,
                    weighted_capacity_to_return=weighted_capacity_to_return,
                )

                if decision.requires_capacity_reduction:
                    new_target_capacity = max(0, target_capacity - weighted_capacity_to_return)
                    new_on_demand_capacity = min(on_demand_capacity, new_target_capacity)

                    self._logger.info(
                        "Reducing %s Spot Fleet %s capacity from %s to %s "
                        "(weighted_capacity_to_return=%s) before terminating instances",
                        fleet_type,
                        fleet_id,
                        target_capacity,
                        new_target_capacity,
                        weighted_capacity_to_return,
                    )

                    self._retry(
                        self._aws_client.ec2_client.modify_spot_fleet_request,
                        operation_type="critical",
                        SpotFleetRequestId=fleet_id,
                        TargetCapacity=new_target_capacity,
                        OnDemandTargetCapacity=new_on_demand_capacity,
                    )

                self._aws_ops.terminate_instances_with_fallback(
                    instance_ids, self._request_adapter, f"SpotFleet-{fleet_id} instances"
                )
                self._logger.info("Terminated Spot Fleet %s instances: %s", fleet_id, instance_ids)

                # Determine whether all fleet instances have been returned.
                # decision.is_full_return is based on the weighted capacity sum, so it
                # correctly handles weighted fleets.  The secondary instance-count check
                # below acts as a defensive net for races.
                should_cancel_fleet = decision.is_full_return
                if not should_cancel_fleet and decision.has_fleet_record:
                    should_cancel_fleet = self._fleet_has_no_remaining_instances(
                        fleet_id, set(instance_ids)
                    )
                    if should_cancel_fleet:
                        self._logger.info(
                            "Spot Fleet %s has no remaining active instances "
                            "(weighted-capacity case); treating as full return",
                            fleet_id,
                        )
                        # Zero the capacity to prevent replacement before cancellation.
                        if decision.requires_capacity_reduction:
                            try:
                                self._retry(
                                    self._aws_client.ec2_client.modify_spot_fleet_request,
                                    operation_type="critical",
                                    SpotFleetRequestId=fleet_id,
                                    TargetCapacity=0,
                                    OnDemandTargetCapacity=0,
                                )
                            except Exception as exc:
                                self._logger.warning(
                                    "Failed to zero Spot Fleet %s capacity before cancellation: %s",
                                    fleet_id,
                                    exc,
                                )

                if should_cancel_fleet and decision.has_fleet_record:
                    self._logger.info("Spot Fleet %s is empty, cancelling fleet", fleet_id)
                    self._retry(
                        self._aws_client.ec2_client.cancel_spot_fleet_requests,
                        operation_type="critical",
                        SpotFleetRequestIds=[fleet_id],
                        TerminateInstances=False,
                    )
                    self._maybe_cleanup_launch_template(fleet_details, fleet_config, request_id)
            else:
                # No specific instances — cancel the entire fleet
                self._retry(
                    self._aws_client.ec2_client.cancel_spot_fleet_requests,
                    operation_type="critical",
                    SpotFleetRequestIds=[fleet_id],
                    TerminateInstances=True,
                )
                self._logger.info("Cancelled entire Spot Fleet: %s", fleet_id)
                self._maybe_cleanup_launch_template(fleet_details, fleet_config, request_id)

        except Exception as e:
            self._logger.error("Failed to terminate spot fleet %s: %s", fleet_id, e)
            raise

    def find_fleet_for_instance(self, instance_id: str) -> Optional[str]:
        """Find the Spot Fleet request ID for a specific instance by querying active fleets.

        Args:
            instance_id: EC2 instance ID to search for.

        Returns:
            Spot Fleet request ID if found, None otherwise.
        """
        try:
            fleets = self._retry(
                lambda: self._paginate(
                    self._aws_client.ec2_client.describe_spot_fleet_requests,
                    "SpotFleetRequestConfigs",
                    SpotFleetRequestStates=["active", "modifying"],
                ),
                operation_type="read_only",
            )

            for fleet in fleets:
                fleet_id = fleet.get("SpotFleetRequestId")
                if not fleet_id:
                    continue

                try:
                    fleet_instances = self._retry(
                        lambda fid=fleet_id: self._paginate(
                            self._aws_client.ec2_client.describe_spot_fleet_instances,
                            "ActiveInstances",
                            SpotFleetRequestId=fid,
                        ),
                        operation_type="read_only",
                    )
                    for instance in fleet_instances:
                        if instance.get("InstanceId") == instance_id:
                            return fleet_id
                except Exception as e:
                    self._logger.debug(
                        "Failed to check fleet %s for instance %s: %s", fleet_id, instance_id, e
                    )
                    continue

        except Exception as e:
            self._logger.debug("Failed to find Spot Fleet for instance %s: %s", instance_id, e)

        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fleet_has_no_remaining_instances(self, fleet_id: str, excluded_ids: set[str]) -> bool:
        """Return True when the Spot Fleet has no active instances outside *excluded_ids*.

        Used as a secondary full-return detector for weighted fleets where the
        capacity arithmetic alone is insufficient.

        Args:
            fleet_id: Spot Fleet request ID to inspect.
            excluded_ids: Instance IDs that have already been submitted for
                termination and should be treated as gone.

        Returns:
            True when no active instances remain, False when any do (or on error).
        """
        try:
            resp = self._retry(
                self._aws_client.ec2_client.describe_spot_fleet_instances,
                operation_type="read_only",
                SpotFleetRequestId=fleet_id,
            )
            active = resp.get("ActiveInstances", [])
            remaining = [inst for inst in active if inst.get("InstanceId") not in excluded_ids]
            return len(remaining) == 0
        except Exception as exc:
            self._logger.warning(
                "Could not verify remaining instances for Spot Fleet %s: %s — "
                "assuming non-empty (safe default)",
                fleet_id,
                exc,
            )
            return False

    def _sum_weighted_capacity(
        self,
        fleet_id: str,
        fleet_config: dict[str, Any],
        instance_ids: list[str],
    ) -> int:
        """Return the total WeightedCapacity consumed by *instance_ids* in this Spot Fleet.

        Queries ``describe_spot_fleet_instances`` (which includes ``WeightedCapacity``
        directly on each ``ActiveInstances`` entry) to resolve each returning instance's
        weight.  Falls back to the ``LaunchSpecifications`` / ``LaunchTemplateConfigs``
        weight-by-type map when an instance is absent from ``ActiveInstances``
        (already terminated or in a race).  Instances with no resolvable weight
        default to 1.

        Args:
            fleet_id: Spot Fleet request ID.
            fleet_config: ``SpotFleetRequestConfig`` dict from the describe response.
            instance_ids: The specific instance IDs being returned.

        Returns:
            Sum of weighted capacity units to subtract from TargetCapacity.
        """
        # Build a fallback map of instance_type → WeightedCapacity from the fleet spec.
        weight_by_type: dict[str, int] = {}
        for spec in fleet_config.get("LaunchSpecifications", []):
            itype = spec.get("InstanceType")
            raw_weight = spec.get("WeightedCapacity")
            if itype and raw_weight is not None:
                try:
                    weight_by_type[itype] = int(float(raw_weight))
                except (TypeError, ValueError):
                    pass
        for lt_config in fleet_config.get("LaunchTemplateConfigs", []):
            for override in lt_config.get("Overrides", []):
                itype = override.get("InstanceType")
                raw_weight = override.get("WeightedCapacity")
                if itype and raw_weight is not None:
                    try:
                        weight_by_type[itype] = int(float(raw_weight))
                    except (TypeError, ValueError):
                        pass

        # Fetch the active instance list; the API returns WeightedCapacity per entry.
        weight_by_instance_id: dict[str, int] = {}
        instance_type_by_id: dict[str, str] = {}
        try:
            resp = self._retry(
                self._aws_client.ec2_client.describe_spot_fleet_instances,
                operation_type="read_only",
                SpotFleetRequestId=fleet_id,
            )
            for item in resp.get("ActiveInstances", []):
                iid = item.get("InstanceId")
                itype = item.get("InstanceType")
                raw_weight = item.get("WeightedCapacity")
                if iid:
                    if itype:
                        instance_type_by_id[iid] = itype
                    if raw_weight is not None:
                        try:
                            weight_by_instance_id[iid] = int(float(raw_weight))
                        except (TypeError, ValueError):
                            pass
        except Exception as exc:
            self._logger.warning(
                "Could not fetch active instances for Spot Fleet %s to compute "
                "weighted capacity; defaulting all instance weights to 1: %s",
                fleet_id,
                exc,
            )

        total = 0
        for iid in instance_ids:
            # Prefer the per-instance weight from the live describe response.
            if iid in weight_by_instance_id:
                total += weight_by_instance_id[iid]
            else:
                # Fall back to the weight-by-type map from the fleet config.
                itype = instance_type_by_id.get(iid)
                if itype and itype in weight_by_type:
                    total += weight_by_type[itype]
                else:
                    # Instance not found or type has no weight → default to 1.
                    total += 1

        if not weight_by_type:
            self._logger.debug(
                "Spot Fleet %s has no WeightedCapacity overrides; "
                "using instance count %d as capacity decrement",
                fleet_id,
                len(instance_ids),
            )

        return max(1, total)

    def _retry(self, func: Any, operation_type: str = "standard", **kwargs: Any) -> Any:
        """Delegate to the injected retry function if available, else call directly."""
        if self._retry_fn is not None:
            return self._retry_fn(func, operation_type=operation_type, **kwargs)
        return func(**kwargs)

    def _paginate(self, client_method: Any, result_key: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Paginate through AWS API results."""
        from orb.providers.aws.infrastructure.utils import paginate

        return paginate(client_method, result_key, **kwargs)

    def _maybe_cleanup_launch_template(
        self, fleet_details: dict[str, Any], fleet_config: dict[str, Any], request_id: str = ""
    ) -> None:
        """Delete the ORB-managed launch template associated with this fleet, if cleanup is enabled."""
        tags: dict[str, str] = {}
        if fleet_config.get("TagSpecifications"):
            tags = {
                t["Key"]: t["Value"]
                for t in fleet_config.get("TagSpecifications", [{}])[0].get("Tags", [])
                if isinstance(t, dict)
            }
        if not tags:
            tags = {t["Key"]: t["Value"] for t in fleet_details.get("Tags", [])}

        resolved_request_id = tags.get("orb:request-id", "") or request_id
        if not resolved_request_id:
            self._logger.warning(
                "Spot Fleet has no orb:request-id tag, skipping launch template cleanup"
            )
            return
        self._cleanup_on_zero_capacity("spot_fleet", resolved_request_id)
