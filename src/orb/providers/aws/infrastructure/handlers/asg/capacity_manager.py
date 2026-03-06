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
                "ASG details missing for %s, terminating instances without ASG operations",
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
            return

        # Detach instances (API limit: 50 per call; use 20 for safety)
        for chunk in self._chunk_list(instance_ids, 20):
            self._retry_with_backoff(
                self._aws_client.autoscaling_client.detach_instances,
                operation_type="critical",
                AutoScalingGroupName=asg_name,
                InstanceIds=chunk,
                ShouldDecrementDesiredCapacity=True,
            )
            self._logger.debug("Detached chunk from ASG %s: %s", asg_name, chunk)
        self._logger.info("Detached instances from ASG %s: %s", asg_name, instance_ids)

        # detach_instances with ShouldDecrementDesiredCapacity=True already decremented
        # the live DesiredCapacity counter in AWS.  Only update MinSize if it would now
        # exceed the post-detach capacity.
        new_capacity = max(0, asg_details["DesiredCapacity"] - len(instance_ids))

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

        if new_capacity == 0:
            self._logger.info("ASG %s capacity is zero, deleting ASG", asg_name)
            self._call_delete_asg(asg_name)
            self._cleanup_on_zero_capacity("asg", asg_name)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

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
