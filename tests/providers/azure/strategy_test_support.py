"""Shared helpers for Azure strategy tests."""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.services.provisioning_service import provider_api_key
from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy


@dataclass
class AzureStrategyHarness:
    """Mutable test harness that feeds explicit dependencies into the strategy."""

    strategy: AzureProviderStrategy | None = None
    handlers: dict[str, Any] = field(default_factory=dict)
    azure_client: Any | None = None
    resource_manager: Any | None = None
    deployment_service: Any | None = None


class _TestAzureHandlerFactory:
    """Minimal handler factory that resolves from the harness-owned handler map."""

    def __init__(self, harness: AzureStrategyHarness) -> None:
        self._harness = harness

    def create_handler(self, handler_type: object) -> Any:
        handler_key = provider_api_key(handler_type)
        handler = self._harness.handlers.get(handler_key)
        if handler is None:
            raise AzureValidationError(f"No handler class registered for type: {handler_key}")
        return handler

    def get_all_handlers(self) -> dict[str, Any]:
        return dict(self._harness.handlers)


def run_operation(coro):
    """Run a coroutine in a fresh event loop for synchronous tests."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def build_strategy_harness(
    *,
    config,
    logger,
    provider_instance_name: str = "azure-default",
    cyclecloud_request_lookup=None,
) -> AzureStrategyHarness:
    """Build a strategy plus mutable dependency holders for focused tests."""
    harness = AzureStrategyHarness()
    handler_factory = _TestAzureHandlerFactory(harness)
    harness.strategy = AzureProviderStrategy(
        config=config,
        logger=logger,
        provider_instance_name=provider_instance_name,
        azure_client_resolver=lambda: harness.azure_client,
        azure_handler_factory_resolver=lambda: handler_factory,
        azure_resource_manager_resolver=lambda: harness.resource_manager,
        azure_deployment_service_resolver=lambda: harness.deployment_service,
        cyclecloud_request_lookup=cyclecloud_request_lookup,
    )
    harness.strategy.initialize()
    return harness
