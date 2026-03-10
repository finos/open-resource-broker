"""AWS Instance Operation Service - Handles instance creation and termination."""

from typing import TYPE_CHECKING, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from orb.providers.base.strategy import ProviderOperation, ProviderResult

if TYPE_CHECKING:
    from orb.providers.aws.infrastructure.adapters.aws_provisioning_adapter import (
        AWSProvisioningAdapter,
    )
    from orb.providers.aws.infrastructure.aws_client import AWSClient


class AWSInstanceOperationService:
    """Service for AWS instance creation and termination operations."""

    def __init__(
        self,
        aws_client: "AWSClient",
        logger: LoggingPort,
        provisioning_adapter: "AWSProvisioningAdapter",
        machine_adapter: AWSMachineAdapter,
        provider_name: Optional[str] = None,
        provider_type: str = "aws",
    ):
        self._aws_client = aws_client
        self._logger = logger
        self._provisioning_adapter = provisioning_adapter
        self._machine_adapter = machine_adapter
        self._provider_name = provider_name
        self._provider_type = provider_type

    async def create_instances(
        self, operation: ProviderOperation, handlers: dict
    ) -> ProviderResult:
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
            from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

            metadata = template_config.get("metadata", {})
            enhanced_config = template_config.copy()

            # Extract metadata fields
            for field in [
                "root_device_volume_size",
                "volume_type",
                "iops",
                "fleet_role",
                "fleet_type",
                "instance_profile",
                "key_name",
                "user_data",
            ]:
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
            from orb.domain.request.aggregate import Request
            from orb.domain.request.value_objects import RequestType

            request = Request.create_new_request(
                request_type=RequestType.ACQUIRE,
                template_id=aws_template.template_id,
                machine_count=count,
                provider_type=self._provider_type,
                provider_name=self._provider_name,
                metadata=operation.parameters.get("request_metadata", {}),
                request_id=operation.parameters.get("request_id"),  # type: ignore[arg-type]
            )
            request.provider_api = provider_api

            # Provision via adapter (includes SSM image resolution)
            adapter_result = await self._provisioning_adapter.provision_resources(
                request, aws_template
            )
            if isinstance(adapter_result, dict) and adapter_result.get("success", True):
                return ProviderResult.success_result(
                    {
                        "resource_ids": adapter_result.get("resource_ids", []),
                        "instances": adapter_result.get("instances", []),
                        "provider_api": provider_api,
                        "count": count,
                        "template_id": aws_template.template_id,
                    },
                    {
                        "method": "provisioning_adapter",
                        "provider_data": adapter_result.get("provider_data", {}),
                    },
                )
            return ProviderResult.error_result(
                adapter_result.get("error_message", "Provisioning failed"),
                "PROVISIONING_ERROR",
            )

        except Exception as e:
            self._logger.error("Failed to create instances: %s", e, exc_info=True)
            return ProviderResult.error_result(str(e), "CREATE_INSTANCES_ERROR")

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
                        request_id=operation.parameters.get("request_id", ""),
                    )
                    return ProviderResult.success_result(
                        {"success": True, "terminated_count": len(instance_ids)},
                        {"method": "provisioning_adapter"},
                    )
                except Exception as e:
                    self._logger.warning(
                        "Provisioning adapter failed, using direct termination: %s", e
                    )

            # Fallback to direct termination
            response = self._aws_client.ec2_client.terminate_instances(InstanceIds=instance_ids)
            terminated_count = len(response.get("TerminatingInstances", []))

            return ProviderResult.success_result(
                {
                    "success": terminated_count == len(instance_ids),
                    "terminated_count": terminated_count,
                },
                {"method": "direct_client"},
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to terminate instances: {e}", "TERMINATE_INSTANCES_ERROR"
            )

    def get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        """Handle instance status query operation."""
        try:
            instance_ids = operation.parameters.get("instance_ids", [])

            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for status query", "MISSING_INSTANCE_IDS"
                )

            provider_api = operation.parameters.get("provider_api", "RunInstances")
            response = self._aws_client.ec2_client.describe_instances(InstanceIds=instance_ids)

            instances = []
            for reservation in response["Reservations"]:
                for aws_instance in reservation["Instances"]:
                    instances.append(
                        self._machine_adapter.create_machine_from_aws_instance(
                            aws_instance,
                            request_id="",
                            provider_api=provider_api,
                            resource_id="",
                        )
                    )

            return ProviderResult.success_result(
                {"instances": instances, "queried_count": len(instance_ids)},
                {"operation": "get_instance_status"},
            )

        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to get instance status: {e}", "GET_INSTANCE_STATUS_ERROR"
            )

    def start_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Handle instance start operation."""
        try:
            instance_ids = operation.parameters.get("instance_ids", [])
            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for start operation", "MISSING_INSTANCE_IDS"
                )
            response = self._aws_client.ec2_client.start_instances(InstanceIds=instance_ids)
            results = {}
            for instance in response.get("StartingInstances", []):
                instance_id = instance["InstanceId"]
                current_state = instance["CurrentState"]["Name"]
                results[instance_id] = current_state in ["pending", "running"]
            return ProviderResult.success_result(
                {"results": results},
                {"operation": "start_instances"},
            )
        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to start instances: {e}", "START_INSTANCES_ERROR"
            )

    def stop_instances(self, operation: ProviderOperation) -> ProviderResult:
        """Handle instance stop operation."""
        try:
            instance_ids = operation.parameters.get("instance_ids", [])
            if not instance_ids:
                return ProviderResult.error_result(
                    "Instance IDs are required for stop operation", "MISSING_INSTANCE_IDS"
                )
            response = self._aws_client.ec2_client.stop_instances(InstanceIds=instance_ids)
            results = {}
            for instance in response.get("StoppingInstances", []):
                instance_id = instance["InstanceId"]
                current_state = instance["CurrentState"]["Name"]
                results[instance_id] = current_state in ["stopping", "stopped"]
            return ProviderResult.success_result(
                {"results": results},
                {"operation": "stop_instances"},
            )
        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to stop instances: {e}", "STOP_INSTANCES_ERROR"
            )
