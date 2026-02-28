"""SpotFleet release manager.

Encapsulates all release/teardown logic for Spot Fleet requests,
keeping SpotFleetHandler focused on orchestration.
"""

from typing import Any, Optional

from domain.base.ports import LoggingPort
from domain.base.ports.configuration_port import ConfigurationPort
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from providers.aws.domain.template.value_objects import AWSFleetType
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.utilities.aws_operations import AWSOperations


class SpotFleetReleaseManager:
    """Handles release and teardown of Spot Fleet resources."""

    def __init__(
        self,
        aws_client: AWSClient,
        aws_ops: AWSOperations,
        request_adapter: Optional[RequestAdapterPort],
        config_port: Optional[ConfigurationPort],
        logger: LoggingPort,
    ) -> None:
        self._aws_client = aws_client
        self._aws_ops = aws_ops
        self._request_adapter = request_adapter
        self._config_port = config_port
        self._logger = logger

    def release(
        self,
        fleet_id: str,
        instance_ids: list[str],
        fleet_details: dict[str, Any],
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
        self._logger.info(
            "Processing Spot Fleet %s with %d instances", fleet_id, len(instance_ids)
        )

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
            fleet_type = str(fleet_config.get("Type", "maintain")).lower()
            target_capacity = int(
                fleet_config.get("TargetCapacity", len(instance_ids or [])) or 0
            )
            on_demand_capacity = int(fleet_config.get("OnDemandTargetCapacity", 0) or 0)
            new_target_capacity = None

            if instance_ids:
                if fleet_type == AWSFleetType.MAINTAIN:
                    new_target_capacity = max(0, target_capacity - len(instance_ids))
                    new_on_demand_capacity = min(on_demand_capacity, new_target_capacity)

                    self._logger.info(
                        "Reducing maintain Spot Fleet %s capacity from %s to %s before terminating instances",
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
                self._logger.info(
                    "Terminated Spot Fleet %s instances: %s", fleet_id, instance_ids
                )

                if fleet_type == AWSFleetType.MAINTAIN and new_target_capacity == 0:
                    self._logger.info(
                        "Maintain Spot Fleet %s capacity is zero, cancelling fleet", fleet_id
                    )
                    self._retry(
                        self._aws_client.ec2_client.cancel_spot_fleet_requests,
                        operation_type="critical",
                        SpotFleetRequestIds=[fleet_id],
                        TerminateInstances=False,
                    )
                    self._cleanup_launch_template(fleet_details, fleet_config)
            else:
                # No specific instances — cancel the entire fleet
                self._retry(
                    self._aws_client.ec2_client.cancel_spot_fleet_requests,
                    operation_type="critical",
                    SpotFleetRequestIds=[fleet_id],
                    TerminateInstances=True,
                )
                self._logger.info("Cancelled entire Spot Fleet: %s", fleet_id)

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
            fleets = self._paginate(
                self._aws_client.ec2_client.describe_spot_fleet_requests,
                "SpotFleetRequestConfigs",
                SpotFleetRequestStates=["active", "modifying"],
            )

            for fleet in fleets:
                fleet_id = fleet.get("SpotFleetRequestId")
                if not fleet_id:
                    continue

                try:
                    fleet_instances = self._paginate(
                        self._aws_client.ec2_client.describe_spot_fleet_instances,
                        "ActiveInstances",
                        SpotFleetRequestId=fleet_id,
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
        """Delegate to AWSOperations retry if available, else call directly."""
        retry_method = getattr(self._aws_ops, "_retry_with_backoff", None)
        if retry_method is not None:
            return retry_method(func, operation_type=operation_type, **kwargs)
        return func(**kwargs)

    def _paginate(self, client_method: Any, result_key: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Paginate through AWS API results."""
        from providers.aws.infrastructure.utils import paginate

        return paginate(client_method, result_key, **kwargs)

    def _cleanup_launch_template(
        self, fleet_details: dict[str, Any], fleet_config: dict[str, Any]
    ) -> None:
        """Delete the ORB-managed launch template associated with this fleet, if configured."""
        if self._config_port is None:
            return

        cleanup: dict[str, Any] = {}
        try:
            cleanup = self._config_port.get_cleanup_config()
        except Exception:
            pass

        if not cleanup.get("enabled", True) or not cleanup.get("resources", {}).get(
            "spot_fleet", True
        ):
            return

        tags: dict[str, str] = {}
        if fleet_config.get("TagSpecifications"):
            tags = {
                t["Key"]: t["Value"]
                for t in fleet_config.get("TagSpecifications", [{}])[0].get("Tags", [])
                if isinstance(t, dict)
            }
        if not tags:
            tags = {t["Key"]: t["Value"] for t in fleet_details.get("Tags", [])}

        request_id = tags.get("orb:request-id", "")
        if request_id:
            self._delete_orb_launch_template(request_id)

    def _delete_orb_launch_template(self, request_id: str) -> None:
        """Delete the ORB-managed launch template for a request, if one exists."""
        from botocore.exceptions import ClientError

        if self._config_port is None:
            self._logger.warning(
                "config_port not injected; skipping launch template cleanup for %s", request_id
            )
            return

        try:
            cleanup = self._config_port.get_cleanup_config()
        except Exception as e:
            self._logger.warning("Could not read cleanup config, skipping LT cleanup: %s", e)
            return

        if not cleanup.get("enabled", True) or not cleanup.get("delete_launch_template", True):
            return

        lt_name = f"{self._config_port.get_resource_prefix('launch_template')}{request_id}"
        dry_run = cleanup.get("dry_run", False)

        try:
            response = self._aws_client.ec2_client.describe_launch_templates(
                LaunchTemplateNames=[lt_name]
            )
            templates = response.get("LaunchTemplates", [])
            if not templates:
                self._logger.debug(
                    "No launch template named %s found; nothing to clean up", lt_name
                )
                return

            lt = templates[0]
            lt_tags = {t["Key"]: t["Value"] for t in lt.get("Tags", [])}
            if lt_tags.get("orb:managed-by") != "open-resource-broker":
                self._logger.warning(
                    "Launch template %s is not ORB-managed; skipping deletion", lt_name
                )
                return

            lt_id = lt["LaunchTemplateId"]

            if dry_run:
                self._logger.info(
                    "[dry-run] Would delete launch template %s (%s) for request %s",
                    lt_name,
                    lt_id,
                    request_id,
                )
                return

            self._aws_client.ec2_client.delete_launch_template(LaunchTemplateId=lt_id)
            self._logger.info(
                "Deleted launch template %s (%s) for request %s", lt_name, lt_id, request_id
            )

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "InvalidLaunchTemplateName.NotFoundException":
                self._logger.debug("Launch template %s not found; nothing to clean up", lt_name)
            else:
                self._logger.warning(
                    "Failed to delete launch template %s for request %s: %s",
                    lt_name,
                    request_id,
                    e,
                )
        except Exception as e:
            self._logger.warning(
                "Unexpected error deleting launch template %s for request %s: %s",
                lt_name,
                request_id,
                e,
            )
