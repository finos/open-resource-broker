"""AWS Auto Scaling Group Handler.

This module provides the Auto Scaling Group (ASG) handler implementation for
managing AWS Auto Scaling Groups through the AWS Auto Scaling API.

The ASG handler enables automatic scaling of EC2 instances based on demand,
health checks, and scaling policies, providing high availability and
cost optimization for long-running workloads.

Key Features:
    - Automatic scaling based on metrics
    - Health check and replacement
    - Multi-AZ deployment support
    - Launch template integration
    - Scaling policies and schedules

Classes:
    ASGHandler: Main handler for Auto Scaling Group operations

Usage:
    This handler is used by the AWS provider to manage Auto Scaling Groups
    for workloads that require automatic scaling and high availability.

Note:
    ASG is ideal for long-running services that need to scale automatically
    based on demand and maintain high availability across multiple AZs.
"""

from typing import Any, Dict, List

from domain.base.dependency_injection import injectable
from domain.base.ports import LoggingPort
from domain.request.aggregate import Request
from infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from infrastructure.error.decorators import handle_infrastructure_exceptions
from providers.aws.domain.template.aggregate import AWSTemplate
from providers.aws.exceptions.aws_exceptions import AWSInfrastructureError
from providers.aws.infrastructure.handlers.base_handler import AWSHandler
from providers.aws.utilities.aws_operations import AWSOperations


@injectable
class ASGHandler(AWSHandler):
    """Handler for Auto Scaling Group operations."""

    def __init__(
        self,
        aws_client,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager,
        request_adapter: RequestAdapterPort = None,
    ):
        """
        Initialize the ASG handler with integrated dependencies.

        Args:
            aws_client: AWS client instance
            logger: Logger for logging messages
            aws_ops: AWS operations utility
            launch_template_manager: Launch template manager for AWS-specific operations
            request_adapter: Optional request adapter for terminating instances
        """
        # Use integrated base class initialization
        super().__init__(aws_client, logger, aws_ops, launch_template_manager, request_adapter)

    @handle_infrastructure_exceptions(context="asg_creation")
    def acquire_hosts(self, request: Request, aws_template: AWSTemplate) -> Dict[str, Any]:
        """
        Create an Auto Scaling Group to acquire hosts.
        Returns structured result with resource IDs and instance data.
        """
        try:
            asg_name = self.aws_ops.execute_with_standard_error_handling(
                operation=lambda: self._create_asg_internal(request, aws_template),
                operation_name="create Auto Scaling Group",
                context="ASG",
            )

            return {
                "success": True,
                "resource_ids": [asg_name],
                "instances": [],  # ASG instances come later
                "provider_data": {"resource_type": "asg"},
            }
        except Exception as e:
            return {
                "success": False,
                "resource_ids": [],
                "instances": [],
                "error_message": str(e),
            }

    def _create_asg_internal(self, request: Request, aws_template: AWSTemplate) -> str:
        """Create ASG with pure business logic."""
        # Validate ASG specific prerequisites
        self._validate_asg_prerequisites(aws_template)

        # Create launch template using the new manager
        launch_template_result = self.launch_template_manager.create_or_update_launch_template(
            aws_template, request
        )

        # Store launch template info in request (if request has this method)
        if hasattr(request, "set_launch_template_info"):
            request.set_launch_template_info(
                launch_template_result.template_id, launch_template_result.version
            )

        # Generate ASG name
        asg_name = f"hf-{request.request_id}"

        # Create ASG configuration
        asg_config = self._create_asg_config(
            asg_name=asg_name,
            aws_template=aws_template,
            request=request,
            launch_template_id=launch_template_result.template_id,
            launch_template_version=launch_template_result.version,
        )

        # Create the ASG with circuit breaker for critical operation
        self._retry_with_backoff(
            self.aws_client.autoscaling_client.create_auto_scaling_group,
            operation_type="critical",
            **asg_config,
        )

        self._logger.info(f"Successfully created Auto Scaling Group: {asg_name}")

        # Add ASG tags
        self._tag_asg(asg_name, aws_template, request)

        # Enable instance protection if specified
        if hasattr(aws_template, "instance_protection") and aws_template.instance_protection:
            self._enable_instance_protection(asg_name)

        # Set instance lifecycle hooks if needed
        if hasattr(aws_template, "lifecycle_hooks") and aws_template.lifecycle_hooks:
            self._set_lifecycle_hooks(asg_name, aws_template.lifecycle_hooks)

        return asg_name

    @handle_infrastructure_exceptions(context="asg_termination")
    def _get_asg_instances(self, asg_name: str) -> List[Dict[str, Any]]:
        """Get instances for a specific ASG."""
        # Get ASG information
        asg_list = self._retry_with_backoff(
            lambda: self._paginate(
                self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                "AutoScalingGroups",
                AutoScalingGroupNames=[asg_name],
            )
        )

        if not asg_list:
            self._logger.warning(f"ASG {asg_name} not found")
            return []

        asg = asg_list[0]
        instance_ids = [instance["InstanceId"] for instance in asg.get("Instances", [])]

        if not instance_ids:
            return []

        return self._get_instance_details(instance_ids)

    def release_hosts(self, request: Request) -> None:
        """Release hosts across all ASGs in the request."""
        try:
            if not request.resource_ids:
                raise AWSInfrastructureError("No ASG names found in request")

            # Process all ASG names instead of just the first one
            for asg_name in request.resource_ids:
                try:
                    if request.machine_references:
                        # Terminate specific instances using existing utility
                        instance_ids = [m.machine_id for m in request.machine_references]
                        self.aws_ops.terminate_instances_with_fallback(
                            instance_ids=instance_ids,
                            context=f"ASG-{asg_name}",
                        )
                    else:
                        # Delete entire ASG
                        self._retry_with_backoff(
                            lambda name=asg_name: self.aws_client.autoscaling_client.delete_auto_scaling_group(
                                AutoScalingGroupName=name, ForceDelete=True
                            ),
                            operation_type="critical",
                        )
                        self._logger.info(f"Deleted Auto Scaling Group: {asg_name}")
                except Exception as e:
                    self._logger.error(f"Failed to terminate ASG {asg_name}: {e}")
                    continue

        except Exception as e:
            self._logger.error(f"Failed to release ASG hosts: {str(e)}")
            raise AWSInfrastructureError(f"Failed to release ASG hosts: {str(e)}")

        # Get instance IDs from machine references
        instance_ids = []
        if request.machine_references:
            instance_ids = [m.machine_id for m in request.machine_references]

        if instance_ids:
            # Get ASG details first
            asg_response = self._retry_with_backoff(
                self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                AutoScalingGroupNames=[request.resource_id],
            )
            if not asg_response["AutoScalingGroups"]:
                raise AWSInfrastructureError(f"ASG {request.resource_id} not found")

            asg = asg_response["AutoScalingGroups"][0]

            # Reduce desired capacity first
            current_capacity = asg["DesiredCapacity"]
            new_capacity = max(0, current_capacity - len(instance_ids))

            self._retry_with_backoff(
                self.aws_client.autoscaling_client.update_auto_scaling_group,
                operation_type="critical",
                AutoScalingGroupName=request.resource_id,
                DesiredCapacity=new_capacity,
                MinSize=min(new_capacity, asg["MinSize"]),
            )
            self._logger.info(f"Reduced ASG {request.resource_id} capacity to {new_capacity}")

            # Detach instances from ASG
            self._retry_with_backoff(
                self.aws_client.autoscaling_client.detach_instances,
                operation_type="critical",
                AutoScalingGroupName=request.resource_id,
                InstanceIds=instance_ids,
                ShouldDecrementDesiredCapacity=True,
            )
            self._logger.info(f"Detached instances from ASG: {instance_ids}")

            # Use consolidated AWS operations utility for instance termination
            self.aws_ops.terminate_instances_with_fallback(
                instance_ids, self._request_adapter, "ASG instances"
            )
            self._logger.info(f"Terminated instances: {instance_ids}")
        else:
            # Delete entire ASG
            self._retry_with_backoff(
                self.aws_client.autoscaling_client.delete_auto_scaling_group,
                operation_type="critical",
                AutoScalingGroupName=request.resource_id,
                ForceDelete=True,
            )
            self._logger.info(f"Deleted Auto Scaling Group: {request.resource_id}")

    def check_hosts_status(self, request: Request) -> List[Dict[str, Any]]:
        """Check the status of instances across all ASGs in the request."""
        try:
            if not request.resource_ids:
                self._logger.info("No ASG names found in request")
                return []

            all_instances = []

            # Process all ASG names instead of just the first one
            for asg_name in request.resource_ids:
                try:
                    asg_instances = self._get_asg_instances(asg_name)
                    if asg_instances:
                        formatted_instances = self._format_instance_data(asg_instances, asg_name)
                        all_instances.extend(formatted_instances)
                except Exception as e:
                    self._logger.error(f"Failed to get instances for ASG {asg_name}: {e}")
                    continue

            return all_instances
        except Exception as e:
            self._logger.error(f"Unexpected error checking ASG status: {str(e)}")
            raise AWSInfrastructureError(f"Failed to check ASG status: {str(e)}")
