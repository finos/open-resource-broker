"""Service for orchestrating parallel deprovisioning operations.

This service extracts deprovisioning logic from command handlers,
following the Single Responsibility Principle.
"""

from __future__ import annotations

import asyncio
from typing import Any

from orb.application.ports.query_bus_port import QueryBusPort
from orb.application.services.machine_coordinates import load_machine_coordinates
from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import ContainerPort, LoggingPort, ProviderSelectionPort


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
            successful_instance_ids: list[str] = []
            failed_instance_ids: list[str] = []
            group_machines_list = list(resource_groups.values())

            for i, result in enumerate(results):
                group_instance_ids = [m.machine_id.value for m in group_machines_list[i]]
                if isinstance(result, Exception):
                    error_count += 1
                    errors.append(str(result))
                    failed_instance_ids.extend(group_instance_ids)
                    self.logger.error("Task %s failed: %s", tasks[i].get_name(), result)
                elif isinstance(result, dict) and result.get("success", False):
                    success_count += 1
                    successful_instance_ids.extend(
                        result.get("successful_instance_ids", group_instance_ids)
                    )
                else:
                    error_count += 1
                    if isinstance(result, dict):
                        errors.append(result.get("error_message", "Unknown error"))
                        group_succeeded = list(result.get("successful_instance_ids") or [])
                        successful_instance_ids.extend(group_succeeded)
                        group_failed = result.get("failed_instance_ids")
                        if group_failed is not None:
                            failed_instance_ids.extend(group_failed)
                        else:
                            succeeded_set = set(group_succeeded)
                            failed_instance_ids.extend(
                                iid for iid in group_instance_ids if iid not in succeeded_set
                            )
                    else:
                        errors.append("Unknown error")
                        failed_instance_ids.extend(group_instance_ids)

            partial = error_count > 0 and len(successful_instance_ids) > 0

            self.logger.info(
                "Deprovisioning completed: %d successful, %d failed (partial=%s)",
                success_count,
                error_count,
                partial,
            )

            return {
                "success": error_count == 0,
                "partial": partial,
                "successful_operations": success_count,
                "failed_operations": error_count,
                "successful_instance_ids": successful_instance_ids,
                "failed_instance_ids": failed_instance_ids,
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
            from orb.application.dto.queries import GetTemplateQuery

            template_query = GetTemplateQuery(template_id=template_id)
            template = await self._query_bus.execute(template_query)

            if not template:
                raise ValueError(f"Template not found: {template_id}")

            # Get scheduler for template formatting
            from orb.application.ports.scheduler_port import SchedulerPort

            scheduler = self._container.get(SchedulerPort)
            template_config = scheduler.format_template_for_provider(template)

            self.logger.info("Using %s handler for resource %s", provider_api, resource_id)

            # Create operation using machine's actual provider context
            from orb.domain.base.operations import (
                Operation as ProviderOperation,
                OperationType as ProviderOperationType,
            )

            # Each machine carries its original provisioning request_id; all machines
            # in a resource group share the same resource so they share the same origin.
            origin_request_id = machines[0].request_id or ""

            operation = ProviderOperation(
                operation_type=ProviderOperationType.TERMINATE_INSTANCES,
                parameters={
                    "instance_ids": instance_ids,
                    "template_config": template_config,
                    "template_id": template_id,
                    "provider_api": provider_api,
                    "resource_id": resource_id,
                    "resource_mapping": {iid: (resource_id, 1) for iid in instance_ids},
                    "machine_coordinates": load_machine_coordinates(self.uow_factory, instance_ids),
                    "request_id": origin_request_id,
                    "request": request,
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                },
            )

            # Get provider configuration
            from orb.domain.base.ports.configuration_port import ConfigurationPort

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
                return {
                    "success": True,
                    "partial": False,
                    "terminated_instances": len(instance_ids),
                    "successful_instance_ids": list(instance_ids),
                    "failed_instance_ids": [],
                }
            else:
                metadata = getattr(result, "metadata", None)
                provider_data: dict[str, Any] = {}
                if isinstance(metadata, dict):
                    raw_provider_data = metadata.get("provider_data")
                    if isinstance(raw_provider_data, dict):
                        provider_data = raw_provider_data

                raw_successful = provider_data.get("successful_instance_ids")
                raw_failed = provider_data.get("failed_instance_ids")
                has_successful = isinstance(raw_successful, (list, tuple))
                has_failed = isinstance(raw_failed, (list, tuple))

                if has_successful:
                    successful_ids = [str(iid) for iid in raw_successful]
                else:
                    successful_ids = []

                if has_failed:
                    failed_ids = [str(iid) for iid in raw_failed]
                else:
                    succeeded_set = set(successful_ids)
                    failed_ids = [iid for iid in instance_ids if iid not in succeeded_set]

                if not has_successful and failed_ids:
                    failed_set = set(failed_ids)
                    successful_ids = [iid for iid in instance_ids if iid not in failed_set]

                if successful_ids and failed_ids:
                    self.logger.warning(
                        "Partial termination for resource %s: %d succeeded, %d failed: %s",
                        resource_id,
                        len(successful_ids),
                        len(failed_ids),
                        result.error_message,
                    )
                    return {
                        "success": False,
                        "partial": True,
                        "terminated_instances": len(successful_ids),
                        "successful_instance_ids": successful_ids,
                        "failed_instance_ids": failed_ids,
                        "error_message": result.error_message,
                    }

                self.logger.error(
                    "Termination failed for resource %s: %s", resource_id, result.error_message
                )
                return {
                    "success": False,
                    "partial": False,
                    "terminated_instances": 0,
                    "successful_instance_ids": [],
                    "failed_instance_ids": failed_ids,
                    "error_message": result.error_message,
                }

        except Exception as e:
            self.logger.error("Failed to process resource group %s: %s", resource_id, e)
            return {
                "success": False,
                "partial": False,
                "terminated_instances": 0,
                "successful_instance_ids": [],
                "failed_instance_ids": [machine.machine_id.value for machine in machines],
                "error_message": str(e),
            }
