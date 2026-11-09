"""SpotFleet release manager.

Encapsulates all release/teardown logic for Spot Fleet requests,
keeping SpotFleetHandler focused on orchestration.
"""

from typing import Any, Callable, Iterable, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_fleet_release import BaseFleetReleaseManager
from orb.providers.aws.infrastructure.handlers.fleet_release_policy import (
    FleetCapacityInput,
    FleetReleaseDecision,
)
from orb.providers.aws.infrastructure.handlers.shared.delete_outcome import (
    check_unsuccessful_fleet_response,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations

_SPOT_FLEET_CANCELLED_STATES = frozenset(
    {"cancelled", "cancelled_running", "cancelled_terminating"}
)


class SpotFleetReleaseManager(BaseFleetReleaseManager):
    """Handles release and teardown of Spot Fleet resources.

    Thin implementation of :class:`BaseFleetReleaseManager` that wires in the
    Spot Fleet-specific AWS API calls:
    - describe_spot_fleet_requests  (fetch details)
    - modify_spot_fleet_request     (capacity reduction / zero)
    - cancel_spot_fleet_requests    (cancel fleet)
    - describe_spot_fleet_instances (check remaining instances / sum weights)
    """

    def __init__(
        self,
        aws_client: AWSClient,
        aws_ops: AWSOperations,
        request_adapter: Optional[RequestAdapterPort],
        cleanup_on_zero_capacity_fn: Callable[..., None],
        logger: LoggingPort,
        retry_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        resolved_retry_fn: Callable[..., Any] = (
            retry_fn
            or getattr(aws_ops, "_retry_with_backoff", None)
            or (lambda fn, operation_type="standard", **kwargs: fn(**kwargs))
        )
        super().__init__(
            aws_client=aws_client,
            aws_ops=aws_ops,
            request_adapter=request_adapter,
            cleanup_on_zero_capacity_fn=cleanup_on_zero_capacity_fn,
            logger=logger,
            retry_fn=resolved_retry_fn,
        )

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
    # BaseFleetReleaseManager abstract method implementations
    # ------------------------------------------------------------------

    def _fleet_label(self) -> str:
        return "Spot Fleet"

    def _is_fleet_terminal(self, fleet_details: dict[str, Any]) -> bool:
        return fleet_details.get("SpotFleetRequestState") in _SPOT_FLEET_CANCELLED_STATES

    def _fleet_instances_still_running(self, fleet_details: dict[str, Any]) -> bool:
        return fleet_details.get("SpotFleetRequestState") == "cancelled_running"

    def _fetch_fleet_details(self, fleet_id: str) -> dict[str, Any]:
        fleet_response = self._retry(
            self._aws_client.ec2_client.describe_spot_fleet_requests,
            operation_type="read_only",
            SpotFleetRequestIds=[fleet_id],
        )
        fleet_configs = fleet_response.get("SpotFleetRequestConfigs", [])
        return fleet_configs[0] if fleet_configs else {}

    def _extract_capacity_input(
        self,
        fleet_id: str,
        fleet_details: dict[str, Any],
        instance_ids: list[str],
    ) -> tuple[FleetCapacityInput, dict[str, Any]]:
        fleet_config = fleet_details.get("SpotFleetRequestConfig", {}) if fleet_details else {}
        fleet_type = fleet_config.get("Type", "maintain")
        target_capacity = int(fleet_config.get("TargetCapacity", len(instance_ids or [])) or 0)
        on_demand_capacity = int(fleet_config.get("OnDemandTargetCapacity", 0) or 0)

        weighted = self._sum_weighted_capacity(
            fleet_id,
            fleet_config,
            instance_ids,
        )

        capacity_input = FleetCapacityInput(
            fleet_type=fleet_type,
            target_capacity_units=target_capacity,
            instances_to_return_count=len(instance_ids),
            instance_weighted_capacity_units=weighted,
        )
        extra: dict[str, Any] = {
            "fleet_config": fleet_config,
            "fleet_type": fleet_type,
            "target_capacity": target_capacity,
            "on_demand_capacity": on_demand_capacity,
            "weighted_capacity_to_return": weighted,
        }
        return capacity_input, extra

    def _reduce_capacity(
        self,
        fleet_id: str,
        capacity_input: FleetCapacityInput,
        extra: dict[str, Any],
        decision: FleetReleaseDecision,
    ) -> None:
        # Re-read the targets right before changing them: the details passed in may
        # be seconds old, and a concurrent return would otherwise be overwritten with
        # a value computed from the stale target. The API has no compare-and-set;
        # returns of one fleet are serialised within this process only.
        target_capacity, on_demand_capacity = self._read_target_capacities(
            fleet_id, extra["target_capacity"], extra["on_demand_capacity"]
        )
        weighted = extra["weighted_capacity_to_return"]
        fleet_type = extra["fleet_type"]

        new_target_capacity = max(0, target_capacity - weighted)
        new_on_demand_capacity = min(on_demand_capacity, new_target_capacity)

        self._logger.info(
            "Reducing %s Spot Fleet %s capacity from %s to %s "
            "(weighted_capacity_to_return=%s) before terminating instances",
            fleet_type,
            fleet_id,
            target_capacity,
            new_target_capacity,
            weighted,
        )

        self._retry(
            self._aws_client.ec2_client.modify_spot_fleet_request,
            operation_type="critical",
            operation_name="modify_spot_fleet_request",
            SpotFleetRequestId=fleet_id,
            TargetCapacity=new_target_capacity,
            OnDemandTargetCapacity=new_on_demand_capacity,
            # The instances being returned are terminated explicitly afterwards.
            # Without this, a fleet that still counts an instance that is already
            # terminating picks an extra instance to terminate when the target
            # drops, and the maintain fleet then launches a replacement.
            ExcessCapacityTerminationPolicy="noTermination",
        )
        # Read the target back: a different value means another process changed it.
        try:
            observed: Optional[int] = self._fetch_fleet_details(fleet_id)["SpotFleetRequestConfig"][
                "TargetCapacity"
            ]
        except Exception as exc:
            self._logger.debug("Could not read back target capacity of %s: %s", fleet_id, exc)
            observed = None
        self._warn_if_target_changed(fleet_id, new_target_capacity, observed)

    def _read_target_capacities(
        self, fleet_id: str, fallback_target: int, fallback_on_demand: int
    ) -> tuple[int, int]:
        """Return the fleet's current ``(target, on-demand target)``, or the fallbacks."""
        try:
            config = self._fetch_fleet_details(fleet_id)["SpotFleetRequestConfig"]
            target = config["TargetCapacity"]
            on_demand = config.get("OnDemandTargetCapacity", 0) or 0
            if not isinstance(target, int) or not isinstance(on_demand, int):
                raise TypeError(f"unexpected capacities {target!r}/{on_demand!r}")
            return target, on_demand
        except Exception as exc:
            self._logger.warning(
                "Could not re-read target capacity of Spot Fleet %s (%s); using %s",
                fleet_id,
                exc,
                fallback_target,
            )
            return int(fallback_target), int(fallback_on_demand)

    def _terminate_instances(self, fleet_id: str, instance_ids: list[str]) -> None:
        self._aws_ops.terminate_instances_with_fallback(
            instance_ids, self._request_adapter, f"SpotFleet-{fleet_id} instances"
        )

    def _cancel_or_delete_fleet(
        self,
        fleet_id: str,
        terminate_instances: bool,
        is_maintain: bool = False,
    ) -> None:
        response = self._retry(
            self._aws_client.ec2_client.cancel_spot_fleet_requests,
            operation_type="critical",
            operation_name="cancel_spot_fleet_requests",
            SpotFleetRequestIds=[fleet_id],
            # A maintain fleet can launch an instance between the return and the
            # cancel; terminating on cancel keeps it from being left running.
            TerminateInstances=terminate_instances or is_maintain,
        )
        check_unsuccessful_fleet_response(response, "spot_fleet", self._fleet_is_cancelled)

    def _fleet_is_cancelled(self, fleet_id: str) -> bool:
        """Return True when describe_spot_fleet_requests reports the request as cancelled."""
        configs = self._retry(
            self._aws_client.ec2_client.describe_spot_fleet_requests,
            operation_type="read_only",
            SpotFleetRequestIds=[fleet_id],
        ).get("SpotFleetRequestConfigs", [])
        return bool(configs) and all(
            c.get("SpotFleetRequestState") in _SPOT_FLEET_CANCELLED_STATES for c in configs
        )

    def _list_fleet_instance_ids(self, fleet_id: str) -> Optional[list[str]]:
        try:
            ids: list[str] = []
            next_token: Optional[str] = None
            while True:
                kwargs: dict[str, Any] = {"SpotFleetRequestId": fleet_id}
                if next_token:
                    kwargs["NextToken"] = next_token
                resp = self._retry(
                    self._aws_client.ec2_client.describe_spot_fleet_instances,
                    operation_type="read_only",
                    **kwargs,
                )
                ids.extend(
                    i["InstanceId"] for i in resp.get("ActiveInstances", []) if i.get("InstanceId")
                )
                next_token = resp.get("NextToken")
                if not isinstance(next_token, str) or not next_token:
                    return ids
        except Exception as exc:
            self._logger.warning("Could not list instances of Spot Fleet %s: %s", fleet_id, exc)
            return None

    def _fleet_has_no_remaining_instances(self, fleet_id: str, excluded_ids: set[str]) -> bool:
        """Return True when the Spot Fleet has no active instances outside *excluded_ids*."""
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

    def _zero_capacity(self, fleet_id: str) -> None:
        self._retry(
            self._aws_client.ec2_client.modify_spot_fleet_request,
            operation_type="critical",
            operation_name="modify_spot_fleet_request",
            SpotFleetRequestId=fleet_id,
            TargetCapacity=0,
            OnDemandTargetCapacity=0,
        )

    def _cleanup_launch_template(
        self,
        fleet_details: dict[str, Any],
        request_id: str = "",
        released_instance_ids: Optional[Iterable[str]] = None,
    ) -> None:
        fleet_config = fleet_details.get("SpotFleetRequestConfig", {}) if fleet_details else {}
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
        self._cleanup_on_zero_capacity("spot_fleet", resolved_request_id, released_instance_ids)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
                    self._logger.debug(
                        "Ignoring non-numeric WeightedCapacity %r for instance "
                        "type %s; treating its weight as 1",
                        raw_weight,
                        itype,
                    )
        for lt_config in fleet_config.get("LaunchTemplateConfigs", []):
            for override in lt_config.get("Overrides", []):
                itype = override.get("InstanceType")
                raw_weight = override.get("WeightedCapacity")
                if itype and raw_weight is not None:
                    try:
                        weight_by_type[itype] = int(float(raw_weight))
                    except (TypeError, ValueError):
                        self._logger.debug(
                            "Ignoring non-numeric WeightedCapacity %r for instance "
                            "type %s; treating its weight as 1",
                            raw_weight,
                            itype,
                        )

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
                            self._logger.debug(
                                "Ignoring non-numeric WeightedCapacity %r for "
                                "instance %s; treating its weight as 1",
                                raw_weight,
                                iid,
                            )
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

    def _paginate(self, client_method: Any, result_key: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Paginate through AWS API results."""
        from orb.providers.aws.infrastructure.utils import paginate

        return paginate(client_method, result_key, **kwargs)
