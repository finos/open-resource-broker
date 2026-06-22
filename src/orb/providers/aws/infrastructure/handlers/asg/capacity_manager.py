"""ASG capacity management.

Encapsulates the two capacity-adjustment responsibilities that precede instance
termination in an Auto Scaling Group:

1. Pre-termination capacity reduction — lower DesiredCapacity and MinSize so
   AWS does not replace the instances we are about to terminate.
2. Instance termination with weight-aware capacity decrement — call
   terminate_instance_in_auto_scaling_group per instance so AWS decrements
   DesiredCapacity by the instance's WeightedCapacity (not by 1), which is
   symmetric with scale-up behaviour in Mixed Instance Policy ASGs.
"""

from typing import Any, Callable, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.utilities.aws_operations import AWSOperations


class ASGCapacityManager:
    """Manages ASG capacity adjustments ahead of instance termination."""

    def __init__(
        self,
        aws_client: AWSClient,
        aws_ops: AWSOperations,
        request_adapter: Optional[RequestAdapterPort],
        cleanup_on_zero_capacity_fn: Callable[[str, str], None],
        logger: LoggingPort,
        retry_with_backoff: Callable,
        chunk_list: Callable,
    ) -> None:
        self._aws_client = aws_client
        self._aws_ops = aws_ops
        self._request_adapter = request_adapter
        self._cleanup_on_zero_capacity = cleanup_on_zero_capacity_fn
        self._logger = logger
        self._retry_with_backoff = retry_with_backoff
        self._chunk_list = chunk_list
        self._delete_asg_fn: Optional[Callable[[str], None]] = None

    def set_delete_asg_fn(self, fn: Callable[[str], None]) -> None:
        """Register the handler's ASG deletion callback."""
        self._delete_asg_fn = fn

    def reduce_capacity(self, instance_ids: list[str]) -> None:
        """Reduce ASG DesiredCapacity and MinSize ahead of instance termination.

        Queries which ASG each instance belongs to, then lowers the group's
        DesiredCapacity by the number of instances being removed and clamps
        MinSize so it does not exceed the new desired value.  All failures are
        warning-only so that a capacity-reduction hiccup never blocks the
        caller's termination flow.
        """
        if not instance_ids:
            return

        instance_group_map: dict[str, list[str]] = {}

        try:
            for chunk in self._chunk_list(instance_ids, 50):
                response = self._retry_with_backoff(
                    self._aws_client.autoscaling_client.describe_auto_scaling_instances,
                    operation_type="read_only",
                    InstanceIds=chunk,
                )
                for entry in response.get("AutoScalingInstances", []):
                    group_name = entry.get("AutoScalingGroupName")
                    instance_id = entry.get("InstanceId")
                    if group_name and instance_id:
                        instance_group_map.setdefault(group_name, []).append(instance_id)
        except Exception as exc:
            self._logger.warning("Failed to map instances to ASGs for capacity reduction: %s", exc)
            return

        if not instance_group_map:
            return

        for group_name, instances in instance_group_map.items():
            try:
                group_response = self._retry_with_backoff(
                    self._aws_client.autoscaling_client.describe_auto_scaling_groups,
                    operation_type="read_only",
                    AutoScalingGroupNames=[group_name],
                )
            except Exception as exc:
                self._logger.warning(
                    "Failed to describe ASG %s while reducing capacity: %s", group_name, exc
                )
                continue

            groups = group_response.get("AutoScalingGroups", [])
            if not groups:
                continue

            asg = groups[0]
            current_desired = asg.get("DesiredCapacity", 0) or 0
            current_min = asg.get("MinSize", 0) or 0

            new_desired = max(0, current_desired - len(instances))
            new_min = min(current_min, new_desired)

            if current_desired == new_desired and current_min == new_min:
                continue

            try:
                self._retry_with_backoff(
                    self._aws_client.autoscaling_client.update_auto_scaling_group,
                    operation_type="critical",
                    AutoScalingGroupName=group_name,
                    DesiredCapacity=new_desired,
                    MinSize=new_min,
                )
                self._logger.info(
                    "Reduced ASG %s capacity from %s to %s before terminating %s instances",
                    group_name,
                    current_desired,
                    new_desired,
                    len(instances),
                )
            except Exception as exc:
                self._logger.warning(
                    "Failed to update ASG %s capacity prior to termination: %s",
                    group_name,
                    exc,
                )

    def release_instances(
        self, asg_name: str, instance_ids: list[str], asg_details: dict[str, Any]
    ) -> None:
        """Terminate instances via the ASG API, adjusting MinSize if needed.

        Uses terminate_instance_in_auto_scaling_group so DesiredCapacity is
        decremented by WeightedCapacity (weight-aware, unlike detach_instances).
        If asg_details is empty the instances are terminated directly without
        attempting ASG-specific operations.  When DesiredCapacity reaches zero
        the ASG and its associated launch template are deleted.
        """
        self._logger.info("Processing ASG %s with %s instances", asg_name, len(instance_ids))

        if not asg_details:
            self._logger.warning(
                "ASG details missing for %s, attempting direct describe before cleanup",
                asg_name,
            )
            try:
                response = self._retry_with_backoff(
                    self._aws_client.autoscaling_client.describe_auto_scaling_groups,
                    operation_type="read_only",
                    AutoScalingGroupNames=[asg_name],
                )
                groups = response.get("AutoScalingGroups", [])
                if groups:
                    asg_details = groups[0]
            except Exception as exc:
                self._logger.warning("Retry describe for ASG %s also failed: %s", asg_name, exc)

        if not asg_details:
            self._logger.warning(
                "ASG details unavailable for %s, terminating instances and proceeding with cleanup",
                asg_name,
            )
            self._aws_ops.terminate_instances_with_fallback(
                instance_ids,
                self._request_adapter,
                f"ASG {asg_name} instances (no ASG details)",
            )
            self._logger.info(
                "Terminated ASG %s instances without ASG operations: %s",
                asg_name,
                instance_ids,
            )
            self._call_delete_asg(asg_name)
            self._cleanup_on_zero_capacity("asg", asg_name)
            return

        # Guard against double-execution: only terminate instances that are currently
        # members of this ASG.  If release_instances is called a second time for the
        # same set (e.g. due to an IN_PROGRESS status retry or a race), the instances
        # are already gone and this check prevents a second DesiredCapacity decrement.
        instances_to_terminate = self._filter_asg_members(asg_name, instance_ids)
        if not instances_to_terminate:
            self._logger.info(
                "ASG %s: all %d instance(s) already terminated/detached — skipping ASG termination",
                asg_name,
                len(instance_ids),
            )
            # Instances may still be running (standalone after a prior partial operation);
            # terminate them directly so the return request can complete.
            self._aws_ops.terminate_instances_with_fallback(
                instance_ids, self._request_adapter, f"ASG {asg_name} instances (already detached)"
            )
            return

        skipped = [i for i in instance_ids if i not in instances_to_terminate]
        if skipped:
            self._logger.info(
                "ASG %s: %d instance(s) already terminated/detached (skipping): %s",
                asg_name,
                len(skipped),
                skipped,
            )

        # Use terminate_instance_in_auto_scaling_group (one instance per API call).
        # Unlike detach_instances — which decrements DesiredCapacity by the *count*
        # of instances (always 1 per instance regardless of weight) — this API
        # decrements DesiredCapacity by the instance's WeightedCapacity, which is
        # symmetric with scale-up in Mixed Instance Policy ASGs.  It also handles
        # both the termination and the capacity decrement atomically, so no
        # separate EC2 terminate step is needed.
        for instance_id in instances_to_terminate:
            self._retry_with_backoff(
                self._aws_client.autoscaling_client.terminate_instance_in_auto_scaling_group,
                operation_type="critical",
                InstanceId=instance_id,
                ShouldDecrementDesiredCapacity=True,
            )
            self._logger.debug(
                "Terminated instance %s in ASG %s with weight-aware capacity decrement",
                instance_id,
                asg_name,
            )
        self._logger.info(
            "Terminated instances in ASG %s (weight-aware): %s", asg_name, instances_to_terminate
        )

        # Re-describe the ASG to get live state after termination.
        # terminate_instance_in_auto_scaling_group decrements DesiredCapacity by
        # WeightedCapacity, so DesiredCapacity == 0 reliably signals an empty fleet
        # for both unweighted and weighted ASGs.  No secondary instance-list check needed.
        live_desired = 0
        try:
            live_response = self._retry_with_backoff(
                self._aws_client.autoscaling_client.describe_auto_scaling_groups,
                operation_type="read_only",
                AutoScalingGroupNames=[asg_name],
            )
            live_groups = live_response.get("AutoScalingGroups", [])
            if live_groups:
                live_desired = live_groups[0].get("DesiredCapacity", 0) or 0
        except Exception as exc:
            self._logger.warning(
                "Failed to re-describe ASG %s after termination, falling back to computed capacity: %s",
                asg_name,
                exc,
            )
            live_desired = max(0, asg_details["DesiredCapacity"] - len(instances_to_terminate))

        new_capacity = max(0, live_desired)

        if asg_details["MinSize"] > new_capacity:
            self._retry_with_backoff(
                self._aws_client.autoscaling_client.update_auto_scaling_group,
                operation_type="critical",
                AutoScalingGroupName=asg_name,
                MinSize=new_capacity,
            )
            self._logger.info("Reduced ASG %s MinSize to %s", asg_name, new_capacity)

        if new_capacity == 0:
            self._logger.info("ASG %s is empty, deleting ASG", asg_name)
            self._call_delete_asg(asg_name)
            self._cleanup_on_zero_capacity("asg", asg_name)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _filter_asg_members(self, asg_name: str, instance_ids: list[str]) -> list[str]:
        """Return the subset of instance_ids that are currently attached to asg_name.

        Used as an idempotency guard in release_instances: if an instance has already
        been terminated or detached (e.g. by a prior call), it is excluded so that
        ShouldDecrementDesiredCapacity=True is not applied a second time.
        """
        if not instance_ids:
            return []
        try:
            response = self._retry_with_backoff(
                self._aws_client.autoscaling_client.describe_auto_scaling_instances,
                operation_type="read_only",
                InstanceIds=instance_ids,
            )
            entries = response.get("AutoScalingInstances", [])
            # If describe returned no entries, the API call did not give us
            # actionable membership info — fall back to processing all
            # instances rather than silently dropping them. This matches the
            # exception-path behaviour below.
            if not entries:
                return list(instance_ids)
            # Only terminate instances that are currently in a state where
            # ShouldDecrementDesiredCapacity=True is meaningful.  Instances in
            # Detaching / Detached / Terminated already had their DesiredCapacity
            # decremented on the first call; including them again would double-count.
            _DETACHABLE_STATES = {"InService", "Standby"}

            # Map: instance_id → lifecycle_state for every entry belonging to asg_name.
            state_by_id = {
                entry["InstanceId"]: entry.get("LifecycleState", "")
                for entry in entries
                if entry.get("AutoScalingGroupName") == asg_name
            }

            if not state_by_id:
                # None of the requested instances appeared in describe output at all.
                # Could mean: (a) instances were never in this ASG, (b) describe had a
                # gap.  Fall back to processing all instances to be safe — the original
                # guard logic below will fall through to terminate_instances_with_fallback.
                return list(instance_ids)

            # Return only the instances in a detachable lifecycle state.
            filtered = [iid for iid in instance_ids if state_by_id.get(iid) in _DETACHABLE_STATES]
            # instances not in state_by_id are not (or no longer) in this ASG;
            # they can be skipped (no longer need weight-aware termination).
            return filtered
        except Exception as exc:
            self._logger.warning(
                "Failed to verify ASG %s membership for instances %s; "
                "proceeding with all instances to avoid leaving them running: %s",
                asg_name,
                instance_ids,
                exc,
            )
            # On error, be conservative: attempt termination for all instances.
            # A "not a member" error from terminate_instance_in_auto_scaling_group
            # is better than skipping a needed capacity decrement.
            return list(instance_ids)

    def _call_delete_asg(self, asg_name: str) -> None:
        """Invoke the registered delete-ASG callback, or fall back to direct deletion."""
        if self._delete_asg_fn is not None:
            self._delete_asg_fn(asg_name)
        else:
            self._delete_asg_direct(asg_name)

    def _delete_asg_direct(self, asg_name: str) -> None:
        try:
            self._logger.info("Deleting ASG %s", asg_name)
            self._retry_with_backoff(
                self._aws_client.autoscaling_client.delete_auto_scaling_group,
                operation_type="critical",
                AutoScalingGroupName=asg_name,
                ForceDelete=True,
            )
            self._logger.info("Successfully deleted ASG %s", asg_name)
        except Exception as exc:
            self._logger.warning("Failed to delete ASG %s: %s", asg_name, exc)
