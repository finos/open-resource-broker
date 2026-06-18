"""EC2 Fleet release manager.

Encapsulates the release/teardown responsibility for EC2 Fleet resources:
finding which fleet owns an instance, reducing maintain-fleet capacity,
terminating instances, and deleting fleets when capacity reaches zero.
"""

from typing import Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.fleet_release_policy import (
    compute_fleet_release_decision,
)
from orb.providers.aws.utilities.aws_operations import AWSOperations


class EC2FleetReleaseManager:
    """Manages release and teardown of EC2 Fleet resources.

    Responsibilities:
    - Locate the EC2 Fleet that owns a given instance (find_fleet_for_instance)
    - Reduce maintain-fleet target capacity before terminating instances
    - Terminate specific instances within a fleet
    - Delete the fleet when capacity reaches zero
    - Clean up the associated ORB launch template when appropriate
    """

    def __init__(
        self,
        aws_client: AWSClient,
        aws_ops: AWSOperations,
        request_adapter: Optional[RequestAdapterPort],
        config_port: Optional[ConfigurationPort],
        logger: LoggingPort,
        retry_fn: Callable[..., Any],
        paginate_fn: Callable[..., Any],
        collect_with_next_token_fn: Callable[..., Any],
        cleanup_on_zero_capacity_fn: Callable[[str, str], None],
    ) -> None:
        self._aws_client = aws_client
        self._aws_ops = aws_ops
        self._request_adapter = request_adapter
        self._config_port = config_port
        self._logger = logger
        self._retry = retry_fn
        self._paginate = paginate_fn
        self._collect_with_next_token = collect_with_next_token_fn
        self._cleanup_on_zero_capacity = cleanup_on_zero_capacity_fn

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def release(
        self,
        fleet_id: str,
        instance_ids: list[str],
        fleet_details: dict[str, Any],
        request_id: str = "",
    ) -> None:
        """Release hosts for a single EC2 Fleet.

        For maintain fleets, reduces target capacity before terminating
        instances to prevent AWS from replacing them. Deletes the fleet
        when capacity reaches zero and cleans up the associated launch
        template.

        For request fleets, terminates instances then deletes the fleet
        (AWS does not auto-delete request fleets) and cleans up the
        associated launch template.

        For instant fleets, the fleet is already deleted by AWS; only
        instance termination and launch template cleanup are performed.

        Args:
            fleet_id: The EC2 Fleet ID to operate on.
            instance_ids: Specific instance IDs to terminate. When empty,
                the entire fleet is deleted.
            fleet_details: Pre-fetched DescribeFleets entry for this fleet,
                or an empty dict to trigger a fresh lookup.
            request_id: ORB request ID used for launch template cleanup when
                the fleet record is no longer available (instant fleet case).
        """
        self._logger.info("Processing EC2 Fleet %s with %d instances", fleet_id, len(instance_ids))

        try:
            if not fleet_details:
                fleet_list = self._retry(
                    lambda: self._paginate(
                        self._aws_client.ec2_client.describe_fleets,
                        "Fleets",
                        FleetIds=[fleet_id],
                    ),
                    operation_type="read_only",
                )

                if not fleet_list:
                    self._logger.warning(
                        "EC2 Fleet %s not found, terminating instances directly", fleet_id
                    )
                    self._aws_ops.terminate_instances_with_fallback(
                        instance_ids,
                        self._request_adapter,
                        f"EC2Fleet-{fleet_id} instances",
                    )
                    if request_id:
                        self._cleanup_on_zero_capacity("ec2_fleet", request_id)
                    return

                fleet_details = fleet_list[0]

            fleet_type = fleet_details.get("Type", "maintain")
            current_capacity = fleet_details.get("TargetCapacitySpecification", {}).get(
                "TotalTargetCapacity", len(instance_ids)
            )

            if instance_ids:
                weighted_capacity_to_return = self._sum_weighted_capacity(
                    fleet_id, fleet_details, instance_ids
                )

                decision = compute_fleet_release_decision(
                    fleet_type=fleet_type,
                    current_capacity=current_capacity,
                    weighted_capacity_to_return=weighted_capacity_to_return,
                )

                if decision.requires_capacity_reduction:
                    new_capacity = max(0, current_capacity - weighted_capacity_to_return)
                    self._logger.info(
                        "Reducing %s fleet %s capacity from %s to %s "
                        "(weighted_capacity_to_return=%s) before terminating instances",
                        fleet_type,
                        fleet_id,
                        current_capacity,
                        new_capacity,
                        weighted_capacity_to_return,
                    )
                    self._retry(
                        self._aws_client.ec2_client.modify_fleet,
                        operation_type="critical",
                        FleetId=fleet_id,
                        TargetCapacitySpecification={"TotalTargetCapacity": new_capacity},
                    )

                self._aws_ops.terminate_instances_with_fallback(
                    instance_ids,
                    self._request_adapter,
                    f"EC2Fleet-{fleet_id} instances",
                )
                self._logger.info("Terminated EC2 Fleet %s instances: %s", fleet_id, instance_ids)

                # Determine whether all fleet instances have been returned.
                # decision.is_full_return is based on the weighted capacity sum, so it
                # correctly handles weighted fleets.  The secondary instance-count check
                # below acts as a defensive net for races where the fleet might have been
                # partially refilled between our capacity-reduce call and here.
                should_delete_fleet = decision.is_full_return
                if not should_delete_fleet and decision.has_fleet_record:
                    # Weighted-capacity fallback: the fleet may be logically empty even
                    # if the capacity arithmetic suggests otherwise.
                    should_delete_fleet = self._fleet_has_no_remaining_instances(
                        fleet_id, set(instance_ids)
                    )
                    if should_delete_fleet:
                        self._logger.info(
                            "EC2 Fleet %s has no remaining active instances "
                            "(weighted-capacity case); treating as full return",
                            fleet_id,
                        )
                        # Force capacity to 0 before deletion to prevent AWS from
                        # launching replacement instances during the delete window.
                        if decision.requires_capacity_reduction:
                            try:
                                self._retry(
                                    self._aws_client.ec2_client.modify_fleet,
                                    operation_type="critical",
                                    FleetId=fleet_id,
                                    TargetCapacitySpecification={"TotalTargetCapacity": 0},
                                )
                            except Exception as exc:
                                self._logger.warning(
                                    "Failed to zero EC2 Fleet %s capacity before deletion: %s",
                                    fleet_id,
                                    exc,
                                )

                if should_delete_fleet and decision.has_fleet_record:
                    self._logger.info("EC2 Fleet %s is empty, deleting fleet", fleet_id)
                    if decision.requires_capacity_reduction:
                        # maintain fleet — use _delete_fleet (TerminateInstances=True)
                        self._delete_fleet(fleet_id)
                    else:
                        # request fleet — instances already terminated, delete without terminating
                        self._retry(
                            self._aws_client.ec2_client.delete_fleets,
                            operation_type="critical",
                            FleetIds=[fleet_id],
                            TerminateInstances=False,
                        )
                    self._maybe_cleanup_launch_template(fleet_details)
                elif not decision.has_fleet_record:
                    # instant fleet — already deleted by AWS; only clean up the launch template.
                    self._maybe_cleanup_launch_template(fleet_details)
            else:
                self._delete_fleet(fleet_id)
                self._maybe_cleanup_launch_template(fleet_details)

        except Exception as e:
            self._logger.error("Failed to terminate EC2 fleet %s: %s", fleet_id, e)
            raise

    def find_fleet_for_instance(self, instance_id: str) -> Optional[str]:
        """Find the EC2 Fleet ID that owns the given instance.

        Queries all active/modifying fleets and checks their active
        instances. Returns None when no owning fleet is found.

        Args:
            instance_id: The EC2 instance ID to look up.

        Returns:
            The EC2 Fleet ID, or None if not found.
        """
        try:
            fleets = self._retry(
                lambda: self._paginate(
                    self._aws_client.ec2_client.describe_fleets,
                    "Fleets",
                    FleetStates=["active", "modifying"],
                ),
                operation_type="read_only",
            )

            for fleet in fleets:
                fleet_id = fleet.get("FleetId")
                if not fleet_id:
                    continue

                try:
                    fleet_instances = self._retry(
                        lambda fid=fleet_id: self._collect_with_next_token(
                            self._aws_client.ec2_client.describe_fleet_instances,
                            "ActiveInstances",
                            FleetId=fid,
                        )
                    )

                    for instance in fleet_instances:
                        if instance.get("InstanceId") == instance_id:
                            return fleet_id

                except Exception as e:
                    self._logger.debug(
                        "Failed to check fleet %s for instance %s: %s",
                        fleet_id,
                        instance_id,
                        e,
                    )
                    continue

        except Exception as e:
            self._logger.debug("Failed to find EC2 Fleet for instance %s: %s", instance_id, e)

        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fleet_has_no_remaining_instances(self, fleet_id: str, excluded_ids: set[str]) -> bool:
        """Return True when the EC2 Fleet has no active instances outside *excluded_ids*.

        Used as a secondary full-return detector for weighted fleets where the
        capacity arithmetic alone is insufficient: a single instance with
        WeightedCapacity > 1 can satisfy a TotalTargetCapacity > 1, but the
        capacity counter only decrements by 1 per instance detached.

        Args:
            fleet_id: EC2 Fleet ID to inspect.
            excluded_ids: Instance IDs that have already been submitted for
                termination and should be treated as gone.

        Returns:
            True when no active instances remain, False when any do (or on error).
        """
        try:
            active = self._collect_with_next_token(
                self._aws_client.ec2_client.describe_fleet_instances,
                "ActiveInstances",
                FleetId=fleet_id,
            )
            remaining = [inst for inst in active if inst.get("InstanceId") not in excluded_ids]
            return len(remaining) == 0
        except Exception as exc:
            self._logger.warning(
                "Could not verify remaining instances for EC2 Fleet %s: %s — "
                "assuming non-empty (safe default)",
                fleet_id,
                exc,
            )
            # Safe default: assume the fleet still has instances rather than
            # accidentally deleting a fleet that has active instances.
            return False

    def _sum_weighted_capacity(
        self,
        fleet_id: str,
        fleet_details: dict[str, Any],
        instance_ids: list[str],
    ) -> int:
        """Return the total WeightedCapacity consumed by *instance_ids* in this fleet.

        Queries ``describe_fleet_instances`` to find the InstanceType for each
        active instance, then looks up the WeightedCapacity for that type in
        ``LaunchTemplateConfigs[].Overrides[].WeightedCapacity``.

        Instances that are not present in ``ActiveInstances`` (already terminated
        or in the middle of a race) default to a weight of 1 so that the capacity
        arithmetic errs on the side of *too small* a decrement rather than leaving
        orphaned capacity that AWS would refill.

        Args:
            fleet_id: EC2 Fleet ID.
            fleet_details: Pre-fetched DescribeFleets entry for this fleet.
            instance_ids: The specific instance IDs being returned.

        Returns:
            Sum of weighted capacity units to subtract from TotalTargetCapacity.
        """
        # Build a map of instance_type → WeightedCapacity from the fleet launch spec.
        weight_by_type: dict[str, int] = {}
        for lt_config in fleet_details.get("LaunchTemplateConfigs", []):
            for override in lt_config.get("Overrides", []):
                itype = override.get("InstanceType")
                raw_weight = override.get("WeightedCapacity")
                if itype and raw_weight is not None:
                    try:
                        weight_by_type[itype] = int(raw_weight)
                    except (TypeError, ValueError):
                        pass

        if not weight_by_type:
            # Fleet has no WeightedCapacity overrides at all — each instance counts as 1.
            # Skip the describe_fleet_instances API call entirely; result is just the count.
            self._logger.debug(
                "EC2 Fleet %s has no WeightedCapacity overrides; "
                "using instance count %d as capacity decrement",
                fleet_id,
                len(instance_ids),
            )
            return max(1, len(instance_ids))

        # Fetch the current active instances so we know each instance's type.
        instance_type_by_id: dict[str, str] = {}
        try:
            active = self._collect_with_next_token(
                self._aws_client.ec2_client.describe_fleet_instances,
                "ActiveInstances",
                FleetId=fleet_id,
            )
            for item in active:
                iid = item.get("InstanceId")
                itype = item.get("InstanceType")
                if iid and itype:
                    instance_type_by_id[iid] = itype
        except Exception as exc:
            self._logger.warning(
                "Could not fetch active instances for EC2 Fleet %s to compute "
                "weighted capacity; defaulting all instance weights to 1: %s",
                fleet_id,
                exc,
            )

        total = 0
        for iid in instance_ids:
            itype = instance_type_by_id.get(iid)
            if itype and itype in weight_by_type:
                total += weight_by_type[itype]
            else:
                # Instance not found in ActiveInstances (already terminated / race),
                # or instance type has no explicit weight → default to 1.
                total += 1

        return max(1, total)

    def _delete_fleet(self, fleet_id: str) -> None:
        """Delete an EC2 Fleet, terminating its instances."""
        try:
            self._logger.info("Deleting EC2 Fleet %s", fleet_id)
            self._retry(
                self._aws_client.ec2_client.delete_fleets,
                operation_type="critical",
                FleetIds=[fleet_id],
                TerminateInstances=True,
            )
            self._logger.info("Successfully deleted EC2 Fleet %s", fleet_id)
        except Exception as e:
            self._logger.warning("Failed to delete EC2 Fleet %s: %s", fleet_id, e)
            self._logger.warning(
                "EC2 Fleet deletion failed, but instance termination completed successfully"
            )

    def _maybe_cleanup_launch_template(self, fleet_details: dict[str, Any]) -> None:
        """Delete the ORB launch template associated with this fleet, if cleanup is enabled."""
        tags = {t["Key"]: t["Value"] for t in fleet_details.get("Tags", [])}
        request_id = tags.get("orb:request-id", "")
        if not request_id:
            self._logger.warning(
                "EC2 Fleet has no orb:request-id tag, skipping launch template cleanup"
            )
            return
        self._cleanup_on_zero_capacity("ec2_fleet", request_id)
