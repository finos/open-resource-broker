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

from datetime import datetime
from typing import Any, Dict, List

from botocore.exceptions import ClientError

from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import LoggingPort
from src.domain.request.aggregate import Request
from src.infrastructure.error.decorators import handle_infrastructure_exceptions
from src.infrastructure.ports.request_adapter_port import RequestAdapterPort
from src.providers.aws.domain.template.aggregate import AWSTemplate
from src.providers.aws.exceptions.aws_exceptions import (
    AWSEntityNotFoundError,
    AWSInfrastructureError,
    AWSValidationError,
)
from src.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from src.providers.aws.utilities.aws_operations import AWSOperations


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
    def release_hosts(self, request: Request) -> None:
        """
        Release hosts from an Auto Scaling Group.

        Args:
            request: The request containing the ASG and machine information
        """
        if not request.resource_id:
            raise AWSInfrastructureError("No ASG name found in request")

        # Get ASG configuration first
        asg_response = self._retry_with_backoff(
            self.aws_client.autoscaling_client.describe_auto_scaling_groups,
            operation_type="read_only",
            AutoScalingGroupNames=[request.resource_id],
        )

        if not asg_response["AutoScalingGroups"]:
            raise AWSEntityNotFoundError(f"ASG {request.resource_id} not found")

        asg = asg_response["AutoScalingGroups"][0]

        # Get instance IDs from machine references
        instance_ids = []
        if request.machine_references:
            instance_ids = [m.machine_id for m in request.machine_references]

        if instance_ids:
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
            self._logger.info(
                f"Reduced ASG {request.resource_id} capacity to {new_capacity}")

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
        """Check the status of instances in the ASG."""
        try:
            if not request.resource_id:
                raise AWSInfrastructureError("No ASG name found in request")

            # Get ASG information with pagination and retry
            asg_list = self._retry_with_backoff(
                lambda: self._paginate(
                    self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                    "AutoScalingGroups",
                    AutoScalingGroupNames=[request.resource_id],
                )
            )

            if not asg_list:
                raise AWSEntityNotFoundError(f"ASG {request.resource_id} not found")

            asg = asg_list[0]

            # Log ASG status
            self._logger.debug(
                f"ASG status: Desired={asg.get('DesiredCapacity')}, "
                f"Current={len(asg.get('Instances', []))}, "
                f"Healthy={self._count_healthy_instances(asg)}"
            )

            # Get instance IDs from ASG
            instance_ids = [instance["InstanceId"] for instance in asg["Instances"]]

            if not instance_ids:
                self._logger.info(f"No instances found in ASG {request.resource_id}")
                return []

            # Get detailed instance information
            return self._get_instance_details(instance_ids)

        except ClientError as e:
            error = self._convert_client_error(e)
            self._logger.error(f"Failed to check ASG status: {str(error)}")
            raise error
        except Exception as e:
            self._logger.error(f"Unexpected error checking ASG status: {str(e)}")
            raise AWSInfrastructureError(f"Failed to check ASG status: {str(e)}")

    def _validate_asg_prerequisites(self, aws_template: AWSTemplate) -> None:
        """Validate ASG specific prerequisites."""
        errors = []

        # First validate common prerequisites
        try:
            self._validate_prerequisites(aws_template)
        except AWSValidationError as e:
            errors.extend(str(e).split("\n"))

        # Validate ASG specific requirements
        if hasattr(aws_template, "lifecycle_hooks") and aws_template.lifecycle_hooks:
            for hook in aws_template.lifecycle_hooks:
                if not hook.get("role_arn"):
                    errors.append(
                        f"IAM role ARN required for lifecycle hook {hook.get('name')}")

        if hasattr(aws_template, "target_group_arns") and aws_template.target_group_arns:
            try:
                self._retry_with_backoff(
                    self.aws_client.elbv2_client.describe_target_groups,
                    TargetGroupArns=aws_template.target_group_arns,
                )
            except Exception as e:
                errors.append(f"Invalid target groups: {str(e)}")

        if errors:
            raise AWSValidationError("\n".join(errors))

    def _create_asg_config(
        self,
        asg_name: str,
        aws_template: AWSTemplate,
        request: Request,
        launch_template_id: str,
        launch_template_version: str,
    ) -> Dict[str, Any]:
        """Create Auto Scaling Group configuration with improved options."""
        asg_config = {
            "AutoScalingGroupName": asg_name,
            "LaunchTemplate": {
                "LaunchTemplateId": launch_template_id,
                "Version": launch_template_version,
            },
            "MinSize": request.machine_count,
            "MaxSize": request.machine_count,
            "DesiredCapacity": request.machine_count,
            "VPCZoneIdentifier": (
                ",".join(aws_template.subnet_ids)
                if aws_template.subnet_ids
                else aws_template.subnet_id
            ),
            "HealthCheckType": getattr(aws_template, "health_check_type", "EC2"),
            "HealthCheckGracePeriod": getattr(aws_template, "health_check_grace_period", 300),
            "Tags": [
                {
                    "Key": "Name",
                    "Value": f"hf-{request.request_id}",
                    "PropagateAtLaunch": True,
                },
                {
                    "Key": "RequestId",
                    "Value": str(request.request_id),
                    "PropagateAtLaunch": True,
                },
                {
                    "Key": "TemplateId",
                    "Value": str(aws_template.template_id),
                    "PropagateAtLaunch": True,
                },
                {"Key": "CreatedBy", "Value": "HostFactory", "PropagateAtLaunch": True},
                {
                    "Key": "CreatedAt",
                    "Value": datetime.utcnow().isoformat(),
                    "PropagateAtLaunch": True,
                },
            ],
        }

        # Add template tags
        if hasattr(aws_template, "tags") and aws_template.tags:
            for key, value in aws_template.tags.items():
                asg_config["Tags"].append(
                    {"Key": key, "Value": value, "PropagateAtLaunch": True})

        # Add target group ARNs if specified
        if hasattr(aws_template, "target_group_arns") and aws_template.target_group_arns:
            asg_config["TargetGroupARNs"] = aws_template.target_group_arns

        # Add mixed instances policy if multiple instance types are specified
        if hasattr(aws_template, "instance_types") and aws_template.instance_types:
            asg_config["MixedInstancesPolicy"] = {
                "LaunchTemplate": {
                    "LaunchTemplateSpecification": {
                        "LaunchTemplateId": launch_template_id,
                        "Version": launch_template_version,
                    },
                    "Overrides": [
                        {"InstanceType": instance_type, "WeightedCapacity": str(weight)}
                        for instance_type, weight in aws_template.instance_types.items()
                    ],
                },
                "InstancesDistribution": {
                    "OnDemandPercentageAboveBaseCapacity": getattr(
                        aws_template, "on_demand_percentage", 0
                    ),
                    "SpotAllocationStrategy": getattr(
                        aws_template, "spot_allocation_strategy", "capacity-optimized"
                    ),
                },
            }

        return asg_config

    def _enable_instance_protection(self, asg_name: str) -> None:
        """Enable instance scale-in protection for the ASG."""
        try:
            self._retry_with_backoff(
                self.aws_client.autoscaling_client.update_auto_scaling_group,
                AutoScalingGroupName=asg_name,
                NewInstancesProtectedFromScaleIn=True,
            )
            self._logger.info(f"Enabled instance protection for ASG: {asg_name}")
        except Exception as e:
            self._logger.warning(f"Failed to enable instance protection: {str(e)}")

    def _set_lifecycle_hooks(self, asg_name: str, hooks: List[Dict[str, Any]]) -> None:
        """Set lifecycle hooks for the ASG."""
        for hook in hooks:
            try:
                self._retry_with_backoff(
                    self.aws_client.autoscaling_client.put_lifecycle_hook,
                    AutoScalingGroupName=asg_name,
                    LifecycleHookName=hook["name"],
                    LifecycleTransition=hook["transition"],
                    RoleARN=hook["role_arn"],
                    NotificationTargetARN=hook.get("target_arn"),
                    NotificationMetadata=hook.get("metadata"),
                    HeartbeatTimeout=hook.get("timeout", 3600),
                )
                self._logger.info(
                    f"Set lifecycle hook {hook['name']} for ASG: {asg_name}")
            except Exception as e:
                self._logger.warning(
                    f"Failed to set lifecycle hook {hook['name']}: {str(e)}")

    def _wait_for_instances_termination(self, asg_name: str, timeout: int = 300) -> None:
        """Wait for all instances in the ASG to terminate."""
        import time

        start_time = time.time()
        while True:
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Timeout waiting for instances to terminate in ASG {asg_name}")

            response = self._retry_with_backoff(
                self.aws_client.autoscaling_client.describe_auto_scaling_groups,
                AutoScalingGroupNames=[asg_name],
            )

            if not response["AutoScalingGroups"]:
                return

            asg = response["AutoScalingGroups"][0]
            if not asg["Instances"]:
                return

            self._logger.info(
                f"Waiting for {len(asg['Instances'])} instances to terminate...")
            time.sleep(10)

    def _count_healthy_instances(self, asg: Dict[str, Any]) -> int:
        """Count number of healthy instances in the ASG."""
        return sum(
            1
            for instance in asg.get("Instances", [])
            if instance.get("HealthStatus") == "Healthy"
            and instance.get("LifecycleState") == "InService"
        )

    def _tag_asg(self, asg_name: str, aws_template: AWSTemplate, request: Request) -> None:
        """Add tags to the ASG."""
        try:
            tags = [
                {
                    "Key": "Name",
                    "Value": f"hf-{request.request_id}",
                    "PropagateAtLaunch": True,
                },
                {
                    "Key": "RequestId",
                    "Value": str(request.request_id),
                    "PropagateAtLaunch": True,
                },
                {
                    "Key": "TemplateId",
                    "Value": str(aws_template.template_id),
                    "PropagateAtLaunch": True,
                },
            ]

            # Add template tags
            if hasattr(aws_template, "tags") and aws_template.tags:
                for key, value in aws_template.tags.items():
                    tags.append({"Key": key, "Value": value, "PropagateAtLaunch": True})

            self._retry_with_backoff(
                self.aws_client.autoscaling_client.create_or_update_tags, Tags=tags
            )
            self._logger.debug(f"Successfully tagged ASG {asg_name}")

        except Exception as e:
            self._logger.warning(f"Failed to tag ASG {asg_name}: {str(e)}")
            # Don't raise the exception as tagging failure is non-critical
