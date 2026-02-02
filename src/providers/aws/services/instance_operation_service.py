"""AWS Instance Operation Service - Handles instance creation and termination."""

from typing import TYPE_CHECKING, Any, Optional

from domain.base.ports import LoggingPort
from providers.base.strategy import ProviderOperation, ProviderOperationType, ProviderResult

if TYPE_CHECKING:
    from providers.aws.infrastructure.aws_client import AWSClient
    from providers.aws.infrastructure.adapters.aws_provisioning_adapter import AWSProvisioningAdapter


class AWSInstanceOperationService:
    """Service for AWS instance creation and termination operations."""

    def __init__(
        self,
        aws_client: "AWSClient",
        logger: LoggingPort,
        provisioning_adapter: Optional["AWSProvisioningAdapter"] = None,
        provider_name: Optional[str] = None,
        provider_type: str = "aws",
    ):
        self._aws_client = aws_client
        self._logger = logger
        self._provisioning_adapter = provisioning_adapter
        self._provider_name = provider_name
        self._provider_type = provider_type

    async def create_instances(self, operation: ProviderOperation, handlers: dict) -> ProviderResult:
        """Handle instance creation operation."""
        try:
            template_config = operation.parameters.get("template_config", {})
            count = operation.parameters.get("count", 1)

            if not template_config:
                return ProviderResult.error_result(
                    "Template configuration is required for instance creation",
                    "MISSING_TEMPLATE_CONFIG",
                )

            provider_api = template_config.get("provider_api", "RunInstances")
            handler = handlers.get(provider_api) or handlers.get("RunInstances")
            
            if not handler:
                return ProviderResult.error_result(
                    f"No handler available for provider_api: {provider_api}",
                    "HANDLER_NOT_FOUND",
                )

            # Convert template_config to AWSTemplate
            from providers.aws.domain.template.aws_template_aggregate import AWSTemplate
            
            metadata = template_config.get("metadata", {})
            enhanced_config = template_config.copy()

            # Extract metadata fields
            for field in ["root_device_volume_size", "volume_type", "iops", "fleet_role", 
                         "fleet_type", "instance_profile", "key_name", "user_data"]:
                if not enhanced_config.get(field) and metadata.get(field):
                    enhanced_config[field] = metadata.get(field)

            try:
                aws_template = AWSTemplate.model_validate(enhanced_config)
            except Exception as e:
                self._logger.error("Failed to create AWSTemplate: %s", e)
                aws_template = AWSTemplate(
                    template_id=template_config.get("template_id", "unknown"),
                    image_id=template_config.get("image_id", ""),
                    instance_type=template_config.get("instance_type", "t2.micro"),
                    subnet_ids=template_config.get("subnet_ids", []),
                    security_group_ids=template_config.get("security_group_ids", []),
                )

            # Create request object
            from domain.request.aggregate import Request
            from domain.request.value_objects import RequestType

            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=aws_template.template_id,
                machine_count=count,
                provider_type=self._provider_type,
                provider_name=self._provider_name,
                metadata=operation.parameters.get("request_metadata", {}),
                request_id=operation.parameters.get("request_id"),
            )
            request.provider_api = provider_api

            # Try provisioning adapter first
            if self._provisioning_adapter and not operation.context.get("skip_provisioning_port"):
                try:
                    adapter_result = await self._provisioning_adapter.provision_resources(request, aws_template)
                    if isinstance(adapter_result, dict) and adapter_result.get("success", True):
                        return ProviderResult.success_result(
                            {
                                "resource_ids": adapter_result.get("resource_ids", []),
                                "instances": adapter_result.get("instances", []),
                                "provider_api": provider_api,
                                "count": count,
                                "template_id": aws_template.template_id,
                            },
                            {"method": "provisioning_adapter", "provider_data": adapter_result.get("provider_data", {})},
                        )
                except Exception as e:
                    self._logger.error("Provisioning adapter failed: %s", e)

            # Fallback to handler
            handler_result = handler.acquire_hosts(request, aws_template)
            
            if isinstance(handler_result, dict):
                resource_ids = handler_result.get("resource_ids", [])
                instances = handler_result.get("instances", [])
                success = handler_result.get("success", True)
                if not success:
                    return ProviderResult.error_result(
                        handler_result.get("error_message", "Handler failed"), "HANDLER_ERROR"
                    )
            else:
                resource_ids = [handler_result] if handler_result else []
                instances = []

            return ProviderResult.success_result(
                {
                    "resource_ids": resource_ids,
                    "instances": instances,
                    "provider_api": provider_api,
                    "count": count,
                    "template_id": aws_template.template_id,
                },
                {"method": "handler"},
            )

        except Exception as e:
            return ProviderResult.error_result(f"Failed to create instances: {e}", "CREATE_INSTANCES_ERROR")

    def terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Handle instance termination operation."""
        try:
            instance_ids = operation.parameters.get("instance_ids", [])
            resource_mapping = operation.parameters.get("resource_mapping", {})

            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for termination", "MISSING_INSTANCE_IDS"
                )

            # Try provisioning adapter first
            if self._provisioning_adapter:
                try:
                    self._provisioning_adapter.release_resources(
                        machine_ids=instance_ids,
                        template_id=operation.parameters.get("template_id", "termination-template"),
                        provider_api=operation.parameters.get("provider_api", "RunInstances"),
                        context={},
                        resource_mapping=resource_mapping,
                    )
                    return ProviderResult.success_result(
                        {"success": True, "terminated_count": len(instance_ids)},
                        {"method": "provisioning_adapter"},
                    )
                except Exception as e:
                    self._logger.warning("Provisioning adapter failed, using direct termination: %s", e)

            # Fallback to direct termination
            response = self._aws_client.ec2_client.terminate_instances(InstanceIds=instance_ids)
            terminated_count = len(response.get("TerminatingInstances", []))
            
            return ProviderResult.success_result(
                {"success": terminated_count == len(instance_ids), "terminated_count": terminated_count},
                {"method": "direct_client"},
            )

        except Exception as e:
            return ProviderResult.error_result(f"Failed to terminate instances: {e}", "TERMINATE_INSTANCES_ERROR")

    def get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        """Handle instance status query operation."""
        try:
            instance_ids = operation.parameters.get("instance_ids", [])

            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for status query", "MISSING_INSTANCE_IDS"
                )

            response = self._aws_client.ec2_client.describe_instances(InstanceIds=instance_ids)
            
            # Convert AWS instances to domain objects
            machines = []
            for reservation in response["Reservations"]:
                for aws_instance in reservation["Instances"]:
                    machine = self._convert_aws_instance_to_machine(aws_instance)
                    machines.append(machine)

            return ProviderResult.success_result(
                {"machines": machines, "queried_count": len(instance_ids)},
                {"operation": "get_instance_status"},
            )

        except Exception as e:
            return ProviderResult.error_result(f"Failed to get instance status: {e}", "GET_INSTANCE_STATUS_ERROR")

    async def describe_resource_instances(self, operation: ProviderOperation, handlers: dict) -> ProviderResult:
        """Discover instances from resource IDs (ASG, Fleet, etc.)."""
        try:
            resource_ids = operation.parameters.get("resource_ids", [])
            provider_api = operation.parameters.get("provider_api", "RunInstances")
            
            if not resource_ids:
                return ProviderResult.error_result(
                    "Resource IDs are required for instance discovery",
                    "MISSING_RESOURCE_IDS",
                )

            # Get handler for the provider API
            handler = handlers.get(provider_api) or handlers.get("RunInstances")
            if not handler:
                return ProviderResult.error_result(
                    f"No handler available for provider_api: {provider_api}",
                    "HANDLER_NOT_FOUND",
                )

            # Create minimal request for handler
            from domain.request.aggregate import Request
            from domain.request.value_objects import RequestType

            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=operation.parameters.get("template_id", "unknown"),
                machine_count=0,
                provider_type=self._provider_type,
                provider_name=self._provider_name,
            )
            request.resource_ids = resource_ids

            # Query handler for instances using check_hosts_status
            instances = handler.check_hosts_status(request)
            
            return ProviderResult.success_result(
                {"instances": instances, "resource_ids": resource_ids},
                {"operation": "describe_resource_instances"},
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to describe resource instances: {e}",
                "DESCRIBE_RESOURCE_INSTANCES_ERROR",
            )

    def _convert_aws_instance_to_machine(self, aws_instance: dict) -> dict:
        """Convert AWS instance to domain machine format."""
        from domain.machine.machine_status import MachineStatus

        aws_state = aws_instance.get("State", {})
        state_name = aws_state.get("Name", "unknown") if isinstance(aws_state, dict) else str(aws_state)

        status_mapping = {
            "pending": MachineStatus.PENDING,
            "running": MachineStatus.RUNNING,
            "shutting-down": MachineStatus.SHUTTING_DOWN,
            "terminated": MachineStatus.TERMINATED,
            "stopping": MachineStatus.STOPPING,
            "stopped": MachineStatus.STOPPED,
        }
        machine_status = status_mapping.get(state_name, MachineStatus.UNKNOWN)

        return {
            "instance_id": aws_instance.get("InstanceId"),
            "status": machine_status.value,
            "private_ip": aws_instance.get("PrivateIpAddress"),
            "public_ip": aws_instance.get("PublicIpAddress"),
            "launch_time": aws_instance.get("LaunchTime"),
            "instance_type": aws_instance.get("InstanceType"),
            "subnet_id": aws_instance.get("SubnetId"),
            "vpc_id": aws_instance.get("VpcId"),
            "availability_zone": aws_instance.get("Placement", {}).get("AvailabilityZone"),
            "provider_type": "aws",
        }