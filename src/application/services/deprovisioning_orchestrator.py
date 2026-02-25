"""Service for orchestrating parallel deprovisioning operations.

This service extracts deprovisioning logic from command handlers,
following the Single Responsibility Principle.
"""

from __future__ import annotations

import asyncio
from typing import Any

from application.ports.query_bus_port import QueryBusPort
from domain.base import UnitOfWorkFactory
from domain.base.ports import ContainerPort, LoggingPort, ProviderSelectionPort


class DeprovisioningOrchestrator:
    """Orchestrates parallel deprovisioning operations across providers."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        container: ContainerPort,
        query_bus: QueryBusPort,
        provider_selection_port: ProviderSelectionPort,
    ) -> None:
        """Initialize the orchestrator.

        Args:
            uow_factory: Factory for creating unit of work instances
            logger: Logging port for structured logging
            container: DI container for service resolution
            query_bus: Query bus for CQRS queries
            provider_selection_port: Port for provider operations
        """
        self.uow_factory = uow_factory
        self.logger = logger
        self._container = container
        self._query_bus = query_bus
        self._provider_selection_port = provider_selection_port

    async def execute_deprovisioning(
        self, resource_groups: dict[tuple[str, str, str], list[Any]], request: Any
    ) -> dict[str, Any]:
        """Execute deprovisioning for grouped machines in parallel.

        Args:
            resource_groups: Dictionary mapping (provider_name, provider_api, resource_id) to machines
            request: Request aggregate for correlation

        Returns:
            Dictionary with success status, counts, and errors
        """
        try:
            # Create tasks for parallel execution
            tasks = []

            for (provider_name, provider_api, resource_id), machines in resource_groups.items():
                task = asyncio.create_task(
                    self._process_resource_group(
                        provider_name, provider_api, resource_id, machines, request
                    ),
                    name=f"terminate-{provider_name}-{provider_api}-{resource_id}",
                )
                tasks.append(task)

            # Execute all tasks in parallel
            self.logger.info("Executing %d termination operations in parallel", len(tasks))

            # Wait for all tasks to complete
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            success_count = 0
            error_count = 0
            errors = []

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    error_count += 1
                    errors.append(str(result))
                    self.logger.error("Task %s failed: %s", tasks[i].get_name(), result)
                elif isinstance(result, dict) and result.get("success", False):
                    success_count += 1
                else:
                    error_count += 1
                    if isinstance(result, dict):
                        errors.append(result.get("error_message", "Unknown error"))
                    else:
                        errors.append("Unknown error")

            self.logger.info(
                "Deprovisioning completed: %d successful, %d failed", success_count, error_count
            )

            return {
                "success": error_count == 0,
                "successful_operations": success_count,
                "failed_operations": error_count,
                "errors": errors,
            }

        except Exception as e:
            self.logger.error("Parallel deprovisioning execution failed: %s", e, exc_info=True)
            return {"success": False, "error_message": str(e)}

    async def _process_resource_group(
        self,
        provider_name: str,
        provider_api: str,
        resource_id: str,
        machines: list[Any],
        request: Any,
    ) -> dict[str, Any]:
        """Process machines from same resource for termination.

        Args:
            provider_name: Name of the provider instance
            provider_api: API used to create the resource (e.g., "EC2Fleet", "ASG")
            resource_id: Resource identifier
            machines: List of machine objects to terminate
            request: Request aggregate for correlation

        Returns:
            Dictionary with success status and details
        """
        try:
            instance_ids = [machine.machine_id.value for machine in machines]
            template_id = machines[0].template_id

            self.logger.info(
                "Processing resource group %s-%s-%s with %d machines",
                provider_name,
                provider_api,
                resource_id,
                len(machines),
            )

            # Get template for configuration
            from application.dto.queries import GetTemplateQuery

            template_query = GetTemplateQuery(template_id=template_id)
            template = await self._query_bus.execute(template_query)

            if not template:
                raise ValueError(f"Template not found: {template_id}")

            # Get scheduler for template formatting
            from domain.base.ports.scheduler_port import SchedulerPort

            scheduler = self._container.get(SchedulerPort)
            template_config = scheduler.format_template_for_provider(template)

            self.logger.info("Using %s handler for resource %s", provider_api, resource_id)

            # Create operation using machine's actual provider context
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            operation = ProviderOperation(
                operation_type=ProviderOperationType.TERMINATE_INSTANCES,
                parameters={
                    "instance_ids": instance_ids,
                    "template_config": template_config,
                    "template_id": template_id,
                    "provider_api": provider_api,
                    "resource_id": resource_id,
                    "resource_mapping": {
                        iid: (resource_id, len(instance_ids))
                        for iid in instance_ids
                    },
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )

            # Get provider configuration
            from domain.base.ports.configuration_port import ConfigurationPort

            config_manager = self._container.get(ConfigurationPort)
            config_manager.get_provider_instance_config(provider_name)

            # Execute via provider selection port
            result = await self._provider_selection_port.execute_operation(provider_name, operation)

            if result.success:
                self.logger.info(
                    "Successfully terminated %d instances in resource %s",
                    len(instance_ids),
                    resource_id,
                )
                return {"success": True, "terminated_instances": len(instance_ids)}
            else:
                self.logger.error(
                    "Termination failed for resource %s: %s", resource_id, result.error_message
                )
                return {"success": False, "error_message": result.error_message}

        except Exception as e:
            self.logger.error("Failed to process resource group %s: %s", resource_id, e)
            return {"success": False, "error_message": str(e)}
