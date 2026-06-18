"""ASG capacity management.

Encapsulates the two capacity-adjustment responsibilities that precede instance
termination in an Auto Scaling Group:

1. Pre-termination capacity reduction — lower DesiredCapacity and MinSize so
   AWS does not replace the instances we are about to terminate.
2. Instance detachment with capacity decrement — detach specific instances from
   a known ASG, lower MinSize if needed, then terminate.
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
        """Detach instances from an ASG, adjust MinSize if needed, then terminate.

        If asg_details is empty the instances are terminated directly without
        attempting ASG-specific operations.  When DesiredCapacity reaches zero
        after detachment the ASG and its associated launch template are deleted.
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

        # Guard against double-execution: only detach instances that are currently
        # members of this ASG.  If release_instances is called a second time for the
        # same set (e.g. due to an IN_PROGRESS status retry or a race), the instances
        # are already detached and this check prevents a second DesiredCapacity decrement.
        instances_to_detach = self._filter_asg_members(asg_name, instance_ids)
        if not instances_to_detach:
            self._logger.info(
                "ASG %s: all %d instance(s) already detached — skipping detach and capacity decrement",
                asg_name,
                len(instance_ids),
            )
            # Instances may still be running (standalone after a prior partial detach);
            # terminate them directly so the return request can complete.
            self._aws_ops.terminate_instances_with_fallback(
                instance_ids, self._request_adapter, f"ASG {asg_name} instances (already detached)"
            )
            return

        skipped = [i for i in instance_ids if i not in instances_to_detach]
        if skipped:
            self._logger.info(
                "ASG %s: %d instance(s) already detached (skipping): %s",
                asg_name,
                len(skipped),
                skipped,
            )

        # Detach instances (API limit: 50 per call; use 20 for safety)
        for chunk in self._chunk_list(instances_to_detach, 20):
            self._retry_with_backoff(
                self._aws_client.autoscaling_client.detach_instances,
                operation_type="critical",
                AutoScalingGroupName=asg_name,
                InstanceIds=chunk,
                ShouldDecrementDesiredCapacity=True,
            )
            self._logger.debug("Detached chunk from ASG %s: %s", asg_name, chunk)
        self._logger.info("Detached instances from ASG %s: %s", asg_name, instances_to_detach)

        # Re-describe the ASG to get live state after detach.
        # ShouldDecrementDesiredCapacity=True decrements DesiredCapacity by the
        # *number of instances* detached, not by their WeightedCapacity.  For
        # weighted ASGs (e.g. 1 instance with WeightedCapacity=2, DesiredCapacity=2)
        # the live DesiredCapacity after detaching 1 instance would be 1, not 0,
        # even though the ASG has no remaining instances.  Therefore we also examine
        # the live Instances list when available to detect the "fleet is logically
        # empty" case independently of the DesiredCapacity counter.
        live_groups: list = []
        live_desired = 0
        # None  → describe did not return an Instances list (treat as unknown)
        # []    → describe returned an explicit empty list (no instances)
        live_instances_raw: list | None = None
        try:
            live_response = self._retry_with_backoff(
                self._aws_client.autoscaling_client.describe_auto_scaling_groups,
                operation_type="read_only",
                AutoScalingGroupNames=[asg_name],
            )
            live_groups = live_response.get("AutoScalingGroups", [])
            if live_groups:
                live_desired = live_groups[0].get("DesiredCapacity", 0) or 0
                # Only use the instances list if the key is actually present in the
                # response; a missing key is treated as "unknown" rather than "empty".
                if "Instances" in live_groups[0]:
                    live_instances_raw = live_groups[0]["Instances"]
        except Exception as exc:
            self._logger.warning(
                "Failed to re-describe ASG %s after detach, falling back to computed capacity: %s",
                asg_name,
                exc,
            )
            live_desired = max(0, asg_details["DesiredCapacity"] - len(instances_to_detach))

        new_capacity = max(0, live_desired)

        if asg_details["MinSize"] > new_capacity:
            self._retry_with_backoff(
                self._aws_client.autoscaling_client.update_auto_scaling_group,
                operation_type="critical",
                AutoScalingGroupName=asg_name,
                MinSize=new_capacity,
            )
            self._logger.info("Reduced ASG %s MinSize to %s", asg_name, new_capacity)

        self._aws_ops.terminate_instances_with_fallback(
            instance_ids, self._request_adapter, f"ASG {asg_name} instances"
        )
        self._logger.info("Terminated ASG %s instances: %s", asg_name, instance_ids)

        # Determine whether to delete the ASG.
        #
        # Primary check (unweighted case): DesiredCapacity reached 0 — the standard
        # signal that all instances have been returned.
        #
        # Secondary check (weighted-capacity case): the live Instances list is
        # available AND all remaining instances either belong to our detach set
        # or are in a terminal lifecycle state.  This fires when a single heavy
        # instance (WeightedCapacity > 1) satisfies a DesiredCapacity > 1 request,
        # so AWS only decrements DesiredCapacity by 1 on detach, leaving a
        # non-zero value even though the fleet is logically empty.
        #
        # The secondary check is ONLY used when the describe response explicitly
        # included an Instances list (live_instances_raw is not None); if the key
        # is absent we fall back to the capacity-only check to avoid false positives
        # in partial-return scenarios where the mock / response omits Instances.
        asg_is_empty = new_capacity == 0
        if not asg_is_empty and live_instances_raw is not None:
            _terminal_lifecycle = frozenset(
                {"Detaching", "Detached", "Terminating", "Terminated"}
            )
            detached_set = set(instances_to_detach)
            remaining_active = [
                inst
                for inst in live_instances_raw
                if inst.get("LifecycleState", "") not in _terminal_lifecycle
                and inst.get("InstanceId") not in detached_set
            ]
            if not remaining_active:
                self._logger.info(
                    "ASG %s has no remaining active instances after weighted detach "
                    "(DesiredCapacity=%s); treating as empty",
                    asg_name,
                    new_capacity,
                )
                asg_is_empty = True

        if asg_is_empty:
            if new_capacity > 0:
                # Force DesiredCapacity to 0 before deletion so AWS does not
                # attempt to launch replacement instances while we are deleting.
                self._logger.info(
                    "ASG %s: forcing DesiredCapacity to 0 before deletion "
                    "(weighted-capacity case, live desired=%s)",
                    asg_name,
                    new_capacity,
                )
                try:
                    self._retry_with_backoff(
                        self._aws_client.autoscaling_client.update_auto_scaling_group,
                        operation_type="critical",
                        AutoScalingGroupName=asg_name,
                        DesiredCapacity=0,
                        MinSize=0,
                    )
                except Exception as exc:
                    self._logger.warning(
                        "Failed to zero DesiredCapacity for ASG %s before deletion: %s",
                        asg_name,
                        exc,
                    )
            self._logger.info("ASG %s is empty, deleting ASG", asg_name)
            self._call_delete_asg(asg_name)
            self._cleanup_on_zero_capacity("asg", asg_name)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _filter_asg_members(self, asg_name: str, instance_ids: list[str]) -> list[str]:
        """Return the subset of instance_ids that are currently attached to asg_name.

        Used as an idempotency guard in release_instances: if an instance has already
        been detached (e.g. by a prior call), it is excluded so that
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
            # Only detach instances that are currently in a state where
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
            # they can be skipped for detach (will still be terminated downstream).
            return filtered
        except Exception as exc:
            self._logger.warning(
                "Failed to verify ASG %s membership for instances %s; "
                "proceeding with all instances to avoid leaving them running: %s",
                asg_name,
                instance_ids,
                exc,
            )
            # On error, be conservative: attempt detach for all instances.
            # A "not a member" error from detach_instances is better than
            # skipping a needed capacity decrement.
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
