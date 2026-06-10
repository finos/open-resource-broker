"""
AWS Request Adapter

This module provides an adapter for AWS-specific request operations.
It extracts AWS-specific logic from the domain layer.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional

from botocore.exceptions import ClientError

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.providers.aws.infrastructure.aws_client import AWSClient

if TYPE_CHECKING:
    from orb.providers.aws.infrastructure.aws_handler_factory import AWSHandlerFactory


@injectable
class AWSRequestAdapter(RequestAdapterPort):
    """Adapter for AWS-specific request operations."""

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        handler_factory: Optional["AWSHandlerFactory"] = None,
    ) -> None:
        """
        Initialize the adapter.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            handler_factory: Optional handler factory for delegating cancel operations
        """
        self._aws_client = aws_client
        self._logger = logger
        self._handler_factory = handler_factory

    def get_request_status(self, request: Request) -> dict[str, Any]:
        """
        Get AWS-specific status for request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """
        if not request.resource_id:
            return {"status": "unknown", "message": "No resource ID available"}

        try:
            if request.request_type == RequestType.ACQUIRE:
                return self._get_acquire_request_status(request)
            elif request.request_type == RequestType.RETURN:
                return self._get_return_request_status(request)
            else:
                return {
                    "status": "unknown",
                    "message": f"Unknown request type: {request.request_type}",
                }

        except Exception as e:
            self._logger.error("Failed to get request status: %s", str(e))
            return {
                "status": "error",
                "message": f"Failed to get request status: {e!s}",
            }

    def _get_acquire_request_status(self, request: Request) -> dict[str, Any]:
        """
        Get status for acquire request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """
        dispatch: dict[str, Callable[[Request], dict[str, Any]]] = {
            "EC2Fleet": self._get_ec2_fleet_status,
            "SpotFleet": self._get_spot_fleet_status,
            "ASG": self._get_asg_status,
            "RunInstances": self._get_run_instances_status,
        }
        handler = dispatch.get(request.provider_api or "")
        if handler is None:
            return {
                "status": "unknown",
                "message": f"Unknown provider API: {request.provider_api}",
            }
        return handler(request)

    def _get_ec2_fleet_status(self, request: Request) -> dict[str, Any]:
        """
        Get status for EC2 Fleet request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """
        try:
            response = self._aws_client.ec2_client.describe_fleets(FleetIds=[request.resource_id])

            if not response["Fleets"]:
                return {
                    "status": "error",
                    "message": f"Fleet not found: {request.resource_id}",
                }

            fleet = response["Fleets"][0]

            # Get instance information
            instances_response = self._aws_client.ec2_client.describe_fleet_instances(
                FleetId=request.resource_id
            )

            return {
                "status": fleet["FleetState"],
                "target_capacity": fleet["TargetCapacitySpecification"]["TotalTargetCapacity"],
                "fulfilled_capacity": (
                    fleet["FulfilledCapacity"] if "FulfilledCapacity" in fleet else 0
                ),
                "activity_status": fleet.get("ActivityStatus"),
                "instances": [
                    {
                        "instance_id": instance["InstanceId"],
                        "instance_type": instance["InstanceType"],
                        "lifecycle": instance.get("InstanceLifecycle", "on-demand"),
                    }
                    for instance in instances_response.get("ActiveInstances", [])
                ],
                "errors": fleet.get("Errors", []),
            }

        except Exception as e:
            self._logger.error("Failed to get EC2 Fleet status: %s", str(e))
            return {
                "status": "error",
                "message": f"Failed to get EC2 Fleet status: {e!s}",
            }

    def _get_spot_fleet_status(self, request: Request) -> dict[str, Any]:
        """
        Get status for Spot Fleet request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """
        try:
            response = self._aws_client.ec2_client.describe_spot_fleet_requests(
                SpotFleetRequestIds=[request.resource_id]
            )

            if not response["SpotFleetRequestConfigs"]:
                return {
                    "status": "error",
                    "message": f"Spot Fleet request not found: {request.resource_id}",
                }

            fleet = response["SpotFleetRequestConfigs"][0]

            # Get instance information
            instances_response = self._aws_client.ec2_client.describe_spot_fleet_instances(
                SpotFleetRequestId=request.resource_id
            )

            return {
                "status": fleet["SpotFleetRequestState"],
                "target_capacity": fleet["SpotFleetRequestConfig"]["TargetCapacity"],
                "fulfilled_capacity": fleet.get("FulfilledCapacity", 0),
                "activity_status": fleet.get("ActivityStatus"),
                "instances": [
                    {
                        "instance_id": instance["InstanceId"],
                        "instance_type": instance["InstanceType"],
                        "lifecycle": "spot",
                    }
                    for instance in instances_response.get("ActiveInstances", [])
                ],
                "errors": [],
            }

        except Exception as e:
            self._logger.error("Failed to get Spot Fleet status: %s", str(e))
            return {
                "status": "error",
                "message": f"Failed to get Spot Fleet status: {e!s}",
            }

    def _get_asg_status(self, request: Request) -> dict[str, Any]:
        """
        Get status for Auto Scaling Group request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """
        try:
            response = self._aws_client.autoscaling_client.describe_auto_scaling_groups(
                AutoScalingGroupNames=[request.resource_id]
            )

            if not response["AutoScalingGroups"]:
                return {
                    "status": "error",
                    "message": f"Auto Scaling Group not found: {request.resource_id}",
                }

            asg = response["AutoScalingGroups"][0]

            return {
                "status": "active",  # ASGs don't have a specific status field
                "target_capacity": asg["DesiredCapacity"],
                "min_size": asg["MinSize"],
                "max_size": asg["MaxSize"],
                "instances": [
                    {
                        "instance_id": instance["InstanceId"],
                        "lifecycle_state": instance["LifecycleState"],
                        "health_status": instance["HealthStatus"],
                    }
                    for instance in asg["Instances"]
                ],
            }

        except Exception as e:
            self._logger.error("Failed to get ASG status: %s", str(e))
            return {"status": "error", "message": f"Failed to get ASG status: {e!s}"}

    def _get_run_instances_status(self, request: Request) -> dict[str, Any]:
        """
        Get status for RunInstances request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """
        try:
            # For RunInstances, the resource_id is a comma-separated list of instance
            # IDs
            if not request.resource_id:
                return {"status": "error", "message": "No resource ID available"}

            instance_ids = request.resource_id.split(",")

            response = self._aws_client.ec2_client.describe_instances(InstanceIds=instance_ids)

            instances = []
            for reservation in response["Reservations"]:
                for instance in reservation["Instances"]:
                    instances.append(
                        {
                            "instance_id": instance["InstanceId"],
                            "state": instance["State"]["Name"],
                            "instance_type": instance["InstanceType"],
                            "private_ip": instance.get("PrivateIpAddress"),
                            "public_ip": instance.get("PublicIpAddress"),
                        }
                    )

            return {
                "status": "active" if instances else "error",
                "instances": instances,
            }

        except Exception as e:
            self._logger.error("Failed to get RunInstances status: %s", str(e))
            return {
                "status": "error",
                "message": f"Failed to get RunInstances status: {e!s}",
            }

    def _get_return_request_status(self, request: Request) -> dict[str, Any]:
        """
        Get status for return request.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with status information
        """
        try:
            # For return requests, the resource_id is a comma-separated list of
            # instance IDs
            if not request.resource_id:
                return {"status": "error", "message": "No resource ID available"}

            instance_ids = request.resource_id.split(",")

            response = self._aws_client.ec2_client.describe_instances(InstanceIds=instance_ids)

            instances = []
            for reservation in response["Reservations"]:
                for instance in reservation["Instances"]:
                    instances.append(
                        {
                            "instance_id": instance["InstanceId"],
                            "state": instance["State"]["Name"],
                            "instance_type": instance["InstanceType"],
                        }
                    )

            # Check if all instances are terminated
            all_terminated = all(instance["state"] == "terminated" for instance in instances)

            return {
                "status": "complete" if all_terminated else "in_progress",
                "instances": instances,
            }

        except Exception as e:
            self._logger.error("Failed to get return request status: %s", str(e))
            return {
                "status": "error",
                "message": f"Failed to get return request status: {e!s}",
            }

    def terminate_instances(self, instance_ids: list[str]) -> dict[str, Any]:
        """
        Terminate EC2 instances.

        Args:
            instance_ids: List of instance IDs to terminate

        Returns:
            Dictionary with termination results
        """
        try:
            response = self._aws_client.ec2_client.terminate_instances(InstanceIds=instance_ids)

            return {
                "status": "success",
                "terminated_instances": [
                    {
                        "instance_id": instance["InstanceId"],
                        "previous_state": instance["PreviousState"]["Name"],
                        "current_state": instance["CurrentState"]["Name"],
                    }
                    for instance in response["TerminatingInstances"]
                ],
            }

        except ClientError as e:
            if e.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
                self._logger.info(
                    "Instances already terminated (InvalidInstanceID.NotFound) — treating as success"
                )
                return {
                    "status": "success",
                    "terminated_instances": [],
                    "message": "Instances already terminated",
                }
            self._logger.error("Failed to terminate instances: %s", str(e))
            return {
                "status": "error",
                "message": f"Failed to terminate instances: {e!s}",
            }

        except Exception as e:
            self._logger.error("Failed to terminate instances: %s", str(e))
            return {
                "status": "error",
                "message": f"Failed to terminate instances: {e!s}",
            }

    def cancel_fleet_request(self, request: Request) -> dict[str, Any]:
        """
        Cancel fleet request.

        Delegates to the appropriate handler's cancel_resource method when a
        handler factory is available. Falls back to direct AWS API calls
        otherwise.

        Args:
            request: Request domain entity

        Returns:
            Dictionary with cancellation results
        """
        if not request.provider_api:
            return {
                "status": "error",
                "message": "No provider API specified on request",
            }

        if not request.resource_id:
            return {
                "status": "error",
                "message": "No resource ID specified on request",
            }

        if self._handler_factory is not None:
            try:
                handler = self._handler_factory.create_handler(request.provider_api)
                return handler.cancel_resource(
                    resource_id=request.resource_id,
                    request_id=str(request.request_id) if request.request_id else "",
                )
            except Exception as e:
                self._logger.error("Failed to cancel fleet request via handler: %s", str(e))
                return {
                    "status": "error",
                    "message": f"Failed to cancel fleet request: {e!s}",
                }

        # Fallback: no handler factory — use direct AWS API calls
        self._logger.warning(
            "No handler factory available; using direct AWS API for cancel of %s %s",
            request.provider_api,
            request.resource_id,
        )
        return self._cancel_direct(request)

    def _cancel_direct(self, request: Request) -> dict[str, Any]:
        """Cancel a fleet resource via direct AWS API calls (no handler factory)."""
        try:
            provider_api = request.provider_api or ""
            if "EC2Fleet" in provider_api:
                response = self._aws_client.ec2_client.delete_fleets(
                    FleetIds=[request.resource_id], TerminateInstances=True
                )
                return {
                    "status": "success",
                    "successful_fleets": [
                        fleet["FleetId"] for fleet in response["SuccessfulFleetDeletions"]
                    ],
                    "unsuccessful_fleets": [
                        {"fleet_id": fleet["FleetId"], "error": fleet["Error"]["Message"]}
                        for fleet in response["UnsuccessfulFleetDeletions"]
                    ],
                }
            elif "SpotFleet" in provider_api:
                response = self._aws_client.ec2_client.cancel_spot_fleet_requests(
                    SpotFleetRequestIds=[request.resource_id], TerminateInstances=True
                )
                return {
                    "status": "success",
                    "successful_fleets": [
                        fleet["SpotFleetRequestId"] for fleet in response["SuccessfulFleetRequests"]
                    ],
                    "unsuccessful_fleets": [
                        {
                            "fleet_id": fleet["SpotFleetRequestId"],
                            "error": fleet["Error"]["Message"],
                        }
                        for fleet in response["UnsuccessfulFleetRequests"]
                    ],
                }
            elif provider_api == "ASG":
                self._aws_client.autoscaling_client.delete_auto_scaling_group(
                    AutoScalingGroupName=request.resource_id, ForceDelete=True
                )
                return {
                    "status": "success",
                    "message": f"Auto Scaling Group {request.resource_id} deleted",
                }
            else:
                return {
                    "status": "error",
                    "message": f"Unsupported provider API for cancellation: {request.provider_api}",
                }
        except Exception as e:
            self._logger.error("Failed to cancel fleet request: %s", str(e))
            return {
                "status": "error",
                "message": f"Failed to cancel fleet request: {e!s}",
            }
