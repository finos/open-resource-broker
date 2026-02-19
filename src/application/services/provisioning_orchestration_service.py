"""Service for orchestrating provider provisioning operations."""

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from application.services.provider_registry_service import ProviderRegistryService

from domain.base.ports import ContainerPort, LoggingPort
from domain.request.aggregate import Request
from domain.template.template_aggregate import Template
from providers.results import ProviderSelectionResult


@dataclass
class ProvisioningResult:
    """Result of provisioning operation."""

    success: bool
    resource_ids: list[str]
    instance_ids: list[str]
    instances: list[dict[str, Any]]
    provider_data: dict[str, Any]
    error_message: str | None = None


class ProvisioningOrchestrationService:
    """Service for orchestrating provider provisioning operations."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        provider_registry_service: "ProviderRegistryService",
    ):
        self._container = container
        self._logger = logger
        self._provider_registry_service = provider_registry_service

    async def execute_provisioning(
        self, template: Template, request: Request, selection_result: ProviderSelectionResult
    ) -> ProvisioningResult:
        """Execute provisioning via selected provider using registry execution."""
        try:
            from domain.base.ports.scheduler_port import SchedulerPort
            from domain.base.ports.configuration_port import ConfigurationPort
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            scheduler = self._container.get(SchedulerPort)
            config_manager = self._container.get(ConfigurationPort)

            operation = ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={
                    "template_config": scheduler.format_template_for_provider(template),
                    "count": request.requested_count,
                    "request_id": str(request.request_id),
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                    "dry_run": request.metadata.get("dry_run", False),
                },
            )

            config_manager.get_provider_instance_config(selection_result.provider_name)

            result = await self._provider_registry_service.execute_operation(
                selection_result.provider_name, operation
            )

            if result.success:
                self._logger.info("Provider result.data: %s", result.data)
                self._logger.info("Provider result.metadata: %s", result.metadata)

                resource_ids = result.data.get("resource_ids", [])
                instances = result.data.get("instances", [])

                self._logger.info(
                    "Extracted resource_ids: %s (type: %s)",
                    resource_ids,
                    type(resource_ids),
                )
                self._logger.info("Extracted instances: %s instances", len(instances))

                if resource_ids:
                    for i, resource_id in enumerate(resource_ids):
                        self._logger.info(
                            "Resource ID %s: %s (type: %s)",
                            i + 1,
                            resource_id,
                            type(resource_id),
                        )

                return ProvisioningResult(
                    success=True,
                    resource_ids=resource_ids,
                    instance_ids=result.data.get("instance_ids", []),
                    instances=instances,
                    provider_data=result.metadata or {},
                )
            else:
                return ProvisioningResult(
                    success=False,
                    resource_ids=[],
                    instance_ids=[],
                    instances=[],
                    provider_data=result.metadata or {},
                    error_message=result.error_message,
                )

        except Exception as e:
            self._logger.error("Provisioning execution failed: %s", e)
            return ProvisioningResult(
                success=False,
                resource_ids=[],
                instance_ids=[],
                instances=[],
                provider_data={},
                error_message=str(e),
            )
