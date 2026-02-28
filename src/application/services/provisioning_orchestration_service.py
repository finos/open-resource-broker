"""Service for orchestrating provider provisioning operations."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from domain.base.ports.provider_selection_port import ProviderSelectionPort

from domain.base.ports import ContainerPort, LoggingPort, ProviderConfigPort
from domain.base.results import ProviderSelectionResult
from domain.request.aggregate import Request
from domain.template.template_aggregate import Template


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
        provider_selection_port: "ProviderSelectionPort",
        provider_config_port: ProviderConfigPort,
    ):
        self._container = container
        self._logger = logger
        self._provider_selection_port = provider_selection_port
        self._provider_config_port = provider_config_port

    async def execute_provisioning(
        self, template: Template, request: Request, selection_result: ProviderSelectionResult
    ) -> ProvisioningResult:
        """Execute provisioning via selected provider using registry execution."""
        try:
            from domain.base.ports.scheduler_port import SchedulerPort
            from domain.base.operations import Operation as ProviderOperation, OperationType as ProviderOperationType

            scheduler = self._container.get(SchedulerPort)

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

            self._provider_config_port.get_provider_instance_config(selection_result.provider_name)

            result = await self._provider_selection_port.execute_operation(
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
            self._logger.error(
                "Provisioning execution failed for template %s: %s",
                template.template_id if hasattr(template, "template_id") else "unknown",
                e,
                exc_info=True,
                extra={
                    "request_id": str(request.request_id)
                    if hasattr(request, "request_id")
                    else None,
                    "provider_name": selection_result.provider_name if selection_result else None,
                    "error_type": type(e).__name__,
                },
            )
            return ProvisioningResult(
                success=False,
                resource_ids=[],
                instance_ids=[],
                instances=[],
                provider_data={},
                error_message=f"Provisioning failed: {e}",
            )
