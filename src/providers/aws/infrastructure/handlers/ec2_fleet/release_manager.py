"""EC2 Fleet release manager.

Encapsulates the release/teardown responsibility for EC2 Fleet resources:
finding which fleet owns an instance, reducing maintain-fleet capacity,
terminating instances, and deleting fleets when capacity reaches zero.
"""

from typing import Any, Callable, Optional

from domain.base.ports import LoggingPort
from domain.base.ports.configuration_port import ConfigurationPort
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.utilities.aws_operations import AWSOperations


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
    ) -> None:
        """Release hosts for a single EC2 Fleet.

        For maintain fleets, reduces target capacity before terminating
        instances to prevent AWS from replacing them. Deletes the fleet
        when capacity reaches zero and cleans up the associated launch
        template.

        Args:
            fleet_id: The EC2 Fleet ID to operate on.
            instance_ids: Specific instance IDs to terminate. When empty,
                the entire fleet is deleted.
            fleet_details: Pre-fetched DescribeFleets entry for this fleet,
                or an empty dict to trigger a fresh lookup.
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
                    return

                fleet_details = fleet_list[0]

            fleet_type = fleet_details.get("Type", "maintain")

            if instance_ids:
                new_capacity = 0
                if fleet_type == "maintain":
                    current_capacity = fleet_details["TargetCapacitySpecification"][
                        "TotalTargetCapacity"
                    ]
                    new_capacity = max(0, current_capacity - len(instance_ids))

                    self._logger.info(
                        "Reducing maintain fleet %s capacity from %s to %s before terminating instances",
                        fleet_id,
                        current_capacity,
                        new_capacity,
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

                if fleet_type == "maintain" and new_capacity == 0:
                    self._logger.info("EC2 Fleet %s capacity is zero, deleting fleet", fleet_id)
                    self._delete_fleet(fleet_id)
                    self._maybe_cleanup_launch_template(fleet_details)
                else:
                    self._logger.debug(
                        "EC2 Fleet %s is non-maintain type (%s), skipping fleet deletion",
                        fleet_id,
                        fleet_type,
                    )
            else:
                self._delete_fleet(fleet_id)

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
        if request_id:
            self._cleanup_on_zero_capacity("ec2_fleet", request_id)
