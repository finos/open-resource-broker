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
                decision = compute_fleet_release_decision(
                    fleet_type=fleet_type,
                    current_capacity=target_capacity,
                    instances_to_return=len(instance_ids),
                )

                if decision.requires_capacity_reduction:
                    new_target_capacity = max(0, target_capacity - len(instance_ids))
                    new_on_demand_capacity = min(on_demand_capacity, new_target_capacity)

                    self._logger.info(
                        "Reducing %s Spot Fleet %s capacity from %s to %s before terminating instances",
                        fleet_type,
                        fleet_id,
                        target_capacity,
                        new_target_capacity,
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

                if decision.is_full_return and decision.has_fleet_record:
                    self._logger.info("Spot Fleet %s capacity is zero, cancelling fleet", fleet_id)
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
