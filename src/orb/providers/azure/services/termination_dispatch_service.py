"""Azure termination dispatch helpers."""

from __future__ import annotations

from typing import Any, Callable

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.infrastructure.handlers.azure_handler import (
    AzureHandler,
    AzureReleaseContext,
    AzureReleaseProviderData,
    AzureReleaseHostsResult,
)


class AzureTerminationDispatchService:
    """Own the handler fan-out used by Azure termination flows."""

    def __init__(
        self,
        logger: LoggingPort,
        record_pending_cleanup: Callable[[AzureReleaseHostsResult | None], None],
    ) -> None:
        self._logger = logger
        self._record_pending_cleanup = record_pending_cleanup

    def dispatch(
        self,
        *,
        handler: AzureHandler,
        instance_ids: list[str],
        grouped_resource_mapping: dict[str, list[str]],
        default_resource_id: str,
        context: AzureReleaseContext,
    ) -> list[AzureReleaseProviderData]:
        """Fan out release_hosts calls per resource group and collect provider data."""
        termination_provider_data: list[AzureReleaseProviderData] = []

        dispatch_groups = grouped_resource_mapping or {default_resource_id: instance_ids}
        for resource_id, mapped_instance_ids in dispatch_groups.items():
            handler_result = handler.release_hosts(
                machine_ids=mapped_instance_ids,
                resource_id=resource_id,
                context=context,
            )
            self._record_pending_cleanup(handler_result)
            if not isinstance(handler_result, dict):
                continue

            provider_data = handler_result.get("provider_data")
            if isinstance(provider_data, dict):
                termination_provider_data.append(provider_data)

        return termination_provider_data
