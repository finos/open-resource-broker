"""AWS RunInstances Handler.

This module provides the RunInstances handler implementation for managing
individual EC2 instance launches through the AWS EC2 RunInstances API.

The RunInstances handler provides direct control over individual EC2 instance
provisioning with support for both On-Demand and Spot instances, offering
simplicity and predictability for straightforward deployment scenarios.

Key Features:
    - Direct EC2 instance control
    - On-Demand and Spot instance support
    - Simple configuration and management
    - Immediate instance provisioning
    - Fine-grained instance control

Classes:
    RunInstancesHandler: Main handler for individual instance operations

Usage:
    This handler is used by the AWS provider to manage individual EC2
    instances for simple, predictable workloads that don't require
    advanced fleet management capabilities.

Note:
    RunInstances is ideal for simple deployments, development environments,
    and workloads that require predictable instance provisioning.
"""

from datetime import datetime
from typing import Any, Dict, List

from botocore.exceptions import ClientError

from domain.base.dependency_injection import injectable
from domain.base.ports import ErrorHandlingPort, LoggingPort
from domain.request.aggregate import Request
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.infrastructure.launch_template.manager import (
    AWSLaunchTemplateManager,
)
from providers.aws.utilities.aws_operations import AWSOperations


@injectable
class RunInstancesHandler(AWSHandler):
    """Handler for direct EC2 instance operations using RunInstances."""

    def __init__(
        self,
        aws_client,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: RequestAdapterPort = None,
        error_handler: ErrorHandlingPort = None,
    ) -> None:
        """
        Initialize RunInstances handler with integrated dependencies.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            aws_ops: AWS operations utility
            launch_template_manager: Launch template manager for AWS-specific operations
            request_adapter: Optional request adapter for terminating instances
            error_handler: Optional error handling port for exception management
        """
        # Use integrated base class initialization
        super().__init__(
            aws_client,
            logger,
            aws_ops,
            launch_template_manager,
            request_adapter,
            error_handler,
        )

    @handle_infrastructure_exceptions(context="run_instances_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> Dict[str, Any]:
        """
        Create EC2 instances using RunInstances to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            resource_id = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_instances_internal(request, aws_template),
                operation_name="run EC2 instances",
                context="RunInstances",
            )

            # Get instance details immediately
            instance_ids = request.metadata.get("instance_ids", [])
            instance_details = self._get_instance_details(instance_ids)
            instances = self._format_instance_data(instance_details, resource_id)

            return {
                "success": True,
                "resource_ids": [resource_id],
                "instances": instances,
                "provider_data": {"resource_type": "run_instances"},
            }
        except Exception as e:
            return {
                "success": False,
                "resource_ids": [],
                "instances": [],
                "error_message": str(e),
            }

    def _create_instances_internal(self, request: Request, aws_template: AWSTemplate) -> str:
        """Create RunInstances with pure business logic."""
        # Validate prerequisites
        self._validate_prerequisites(aws_template)

        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
        )

        # Store launch template info in request (if request has this method)
        if hasattr(request, "set_launch_template_info"):
            request.set_launch_template_info(
                launch_template_result.template_id, launch_template_result.version
            )

        # Create RunInstances parameters
        run_params = self._create_run_instances_params(
            aws_template=aws_template,
            request=request,
            launch_template_id=launch_template_result.template_id,
            launch_template_version=launch_template_result.version,
        )

        # Execute RunInstances API call with circuit breaker for critical operation
        response = self._retry_with_backoff(
            self.aws_client.ec2_client.run_instances,
            operation_type="critical",
            **run_params,
        )

        # Extract reservation ID and instance IDs from response
        reservation_id = response.get("ReservationId")
        instance_ids = [instance["InstanceId"] for instance in response.get("Instances", [])]

        if not instance_ids:
            raise AWSInfrastructureError("No instances were created by RunInstances")

        if not reservation_id:
            raise AWSInfrastructureError("No reservation ID returned by RunInstances")

        # Use the actual AWS reservation ID as the resource ID
        resource_id = reservation_id

        # Store instance IDs and reservation ID in request metadata for later retrieval
        if not hasattr(request, "metadata"):
            request.metadata = {}
        request.metadata["instance_ids"] = instance_ids
        request.metadata["reservation_id"] = reservation_id
        request.metadata["run_instances_resource_id"] = resource_id

        self._logger.info(
            "Successfully created %s instances via RunInstances with reservation ID %s: %s",
            len(instance_ids),
            reservation_id,
            instance_ids,
        )

        return resource_id

    def _format_instance_data(
        self, instance_details: List[Dict[str, Any]], resource_id: str
    ) -> List[Dict[str, Any]]:
        """Format AWS instance details to standard structure."""
        return [
            {
                "instance_id": inst["InstanceId"],
                "resource_id": resource_id,
                "status": inst["State"],
                "private_ip": inst.get("PrivateIpAddress"),
                "public_ip": inst.get("PublicIpAddress"),
                "launch_time": inst.get("LaunchTime"),
            }
            for inst in instance_details
        ]

    def _create_run_instances_params(
        self,
        aws_template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> Dict[str, Any]:
        """Create RunInstances parameters with launch template."""

        # Base parameters using launch template
        params = {
            "LaunchTemplate": {
                "LaunchTemplateId": launch_template_id,
                "Version": launch_template_version,
            },
            "MinCount": 1,
            "MaxCount": request.requested_count,
        }

        # Add instance type override if specified (overrides launch template)
        if aws_template.instance_type:
            params["InstanceType"] = aws_template.instance_type

        # Handle networking overrides based on launch template source
        if aws_template.launch_template_id:
            # Using existing launch template - need to check what it contains
            # For now, assume we can override (this should be improved to inspect the
            # LT)
            if aws_template.subnet_id:
                params["SubnetId"] = aws_template.subnet_id
            elif aws_template.subnet_ids and len(aws_template.subnet_ids) == 1:
                params["SubnetId"] = aws_template.subnet_ids[0]

            if aws_template.security_group_ids:
                params["SecurityGroupIds"] = aws_template.security_group_ids
        else:
            # We created the launch template ourselves with NetworkInterfaces
            # Don't override networking at API level - AWS will reject it
            # The launch template already contains all networking configuration
            pass

        # Add spot instance configuration if needed
        if aws_template.price_type == "spot":
            params["InstanceMarketOptions"] = {"MarketType": "spot"}

            if aws_template.max_spot_price:
                params["InstanceMarketOptions"]["SpotOptions"] = {
                    "MaxPrice": str(aws_template.max_spot_price)
                }

        # Add additional tags for instances (beyond launch template)
        tag_specifications = [
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"hf-{request.request_id}"},
                    {"Key": "RequestId", "Value": str(request.request_id)},
                    {"Key": "TemplateId", "Value": str(aws_template.template_id)},
                    {"Key": "CreatedBy", "Value": "HostFactory"},
                    {"Key": "CreatedAt", "Value": datetime.utcnow().isoformat()},
                    {"Key": "ProviderApi", "Value": "RunInstances"},
                ],
            }
        ]

        # Add template tags if any
        if aws_template.tags:
            instance_tags = [{"Key": k, "Value": v} for k, v in aws_template.tags.items()]
            tag_specifications[0]["Tags"].extend(instance_tags)

        params["TagSpecifications"] = tag_specifications

        return params

    def check_hosts_status(self, request: Request) -> List[Dict[str, Any]]:
        """Check the status of instances created by RunInstances."""
        try:
            # Get instance IDs from request metadata
            instance_ids = request.metadata.get("instance_ids", [])

            if not instance_ids:
                # If no instance IDs in metadata, try to find instances using resource
                # IDs (reservation IDs)
                if hasattr(request, "resource_ids") and request.resource_ids:
                    self._logger.info(
                        "No instance IDs in metadata, searching by resource IDs: %s",
                        request.resource_ids,
                    )
                    return self._find_instances_by_resource_ids(request.resource_ids)
                else:
                    self._logger.info(
                        "No instance IDs or resource IDs found in request %s", request.request_id
                    )
                    return []

            # Get detailed instance information using instance IDs
            return self._get_instance_details(instance_ids)

        except Exception as e:
            self._logger.error("Unexpected error checking RunInstances status: %s", str(e))
            raise AWSInfrastructureError(f"Failed to check RunInstances status: {str(e)}")

    def _find_instances_by_resource_ids(self, resource_ids: List[str]) -> List[Dict[str, Any]]:
        """Find instances using resource IDs (reservation IDs for RunInstances)."""
        try:
            all_instances = []

            for resource_id in resource_ids:
                # For RunInstances, resource_id is the reservation ID
                # Try to use describe_instances with Filters to find instances by
                # reservation ID
                try:
                    response = self.aws_client.ec2_client.describe_instances(
                        Filters=[{"Name": "reservation-id", "Values": [resource_id]}]
                    )

                    # Extract instances from reservations
                    for reservation in response.get("Reservations", []):
                        for instance in reservation["Instances"]:
                            instance_data = {
                                "InstanceId": instance["InstanceId"],
                                "State": instance["State"]["Name"],
                                "PrivateIpAddress": instance.get("PrivateIpAddress"),
                                "PublicIpAddress": instance.get("PublicIpAddress"),
                                "LaunchTime": (
                                    instance["LaunchTime"].isoformat()
                                    if instance.get("LaunchTime")
                                    else None
                                ),
                                "Tags": instance.get("Tags", []),
                                "InstanceType": instance["InstanceType"],
                            }
                            all_instances.append(instance_data)

                except ClientError as e:
                    if e.response["Error"]["Code"] == "InvalidReservationID.NotFound":
                        self._logger.warning("Reservation ID %s not found", resource_id)
                        continue
                    elif "Filter dicts have not been implemented" in str(e):
                        # Moto doesn't support reservation-id filter, fall back to
                        # describe all instances
                        self._logger.info(
                            "Reservation-id filter not supported (likely moto), falling back to describe all instances"
                        )
                        return self._find_instances_by_tags_fallback(resource_ids)
                    else:
                        raise
                except Exception as e:
                    if "Filter dicts have not been implemented" in str(e):
                        # Moto doesn't support reservation-id filter, fall back to
                        # describe all instances
                        self._logger.info(
                            "Reservation-id filter not supported (likely moto), falling back to describe all instances"
                        )
                        return self._find_instances_by_tags_fallback(resource_ids)
                    else:
                        raise

            self._logger.info(
                "Found %s instances for resource IDs: %s", len(all_instances), resource_ids
            )
            return all_instances

        except Exception as e:
            self._logger.error("Failed to find instances by resource IDs: %s", str(e))
            raise AWSInfrastructureError(f"Failed to find instances by resource IDs: {str(e)}")

    def _find_instances_by_tags_fallback(self, resource_ids: List[str]) -> List[Dict[str, Any]]:
        """Fallback method to find instances by tags when reservation-id filter is not supported."""
        try:
            self._logger.info(
                "FALLBACK: Starting fallback method for resource IDs: %s", resource_ids
            )

            # In mock mode (moto), we can't use reservation-id filter
            # Instead, look for instances with our RequestId tag
            # This assumes the instances were tagged during creation

            # Get all instances and filter by tags
            response = self.aws_client.ec2_client.describe_instances()
            self._logger.info(
                "FALLBACK: Found %s total reservations", len(response.get("Reservations", []))
            )

            matching_instances = []
            for reservation in response.get("Reservations", []):
                reservation_id = reservation["ReservationId"]
                self._logger.info(
                    "FALLBACK: Checking reservation %s against targets %s",
                    reservation_id,
                    resource_ids,
                )

                # Check if this reservation matches any of our resource IDs
                if reservation_id in resource_ids:
                    self._logger.info(
                        "FALLBACK: MATCH! Reservation %s found %s instances",
                        reservation_id,
                        len(reservation["Instances"]),
                    )
                    for instance in reservation["Instances"]:
                        instance_data = {
                            "InstanceId": instance["InstanceId"],
                            "State": instance["State"]["Name"],
                            "PrivateIpAddress": instance.get("PrivateIpAddress"),
                            "PublicIpAddress": instance.get("PublicIpAddress"),
                            "LaunchTime": (
                                instance["LaunchTime"].isoformat()
                                if instance.get("LaunchTime")
                                else None
                            ),
                            "Tags": instance.get("Tags", []),
                            "InstanceType": instance["InstanceType"],
                        }
                        matching_instances.append(instance_data)
                        self._logger.info(
                            "FALLBACK: Added instance %s with IP %s",
                            instance_data["InstanceId"],
                            instance_data["PrivateIpAddress"],
                        )
                else:
                    self._logger.info("FALLBACK: No match for reservation %s", reservation_id)

            self._logger.info(
                "FALLBACK: Returning %s instances for resource IDs: %s",
                len(matching_instances),
                resource_ids,
            )
            return matching_instances

        except Exception as e:
            self._logger.error("FALLBACK: Fallback method failed to find instances: %s", e)
            # Return empty list rather than raising exception to allow graceful
            # degradation
            return []

    def release_hosts(self, request: Request) -> None:
        """
        Release hosts created by RunInstances.

        Args:
            request: The request containing the instance information
        """
        try:
            # Get instance IDs from machine references or metadata
            instance_ids = []

            if request.machine_references:
                instance_ids = [m.machine_id for m in request.machine_references]
            elif hasattr(request, "metadata") and request.metadata.get("instance_ids"):
                instance_ids = request.metadata["instance_ids"]

            if not instance_ids:
                self._logger.warning("No instance IDs found for request %s", request.request_id)
                return

            # Use consolidated AWS operations utility for instance termination
            self.aws_ops.terminate_instances_with_fallback(
                instance_ids, self._request_adapter, "RunInstances instances"
            )
            self._logger.info("Terminated RunInstances instances: %s", instance_ids)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error("Failed to release RunInstances resources: %s", str(error))
            raise error
        except Exception as e:
            self._logger.error("Unexpected error releasing RunInstances resources: %s", str(e))
            raise AWSInfrastructureError(f"Failed to release RunInstances resources: {str(e)}")
