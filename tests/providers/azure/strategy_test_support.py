"""Shared helpers for Azure strategy tests."""

import asyncio
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.exceptions.azure_exceptions import AzureValidationError
from orb.providers.azure.services.provisioning_service import provider_api_key
from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy

_UNSET = object()


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


class AsyncPager:
    """Minimal async iterator helper for Azure SDK pager-shaped tests."""

    def __init__(self, items: list[Any]) -> None:
        self._items = list(items)

    def __aiter__(self) -> "AsyncPager":
        self._iterator = iter(self._items)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def make_azure_template(
    *,
    template_id: str,
    provider_api: str,
    vm_size: str = "Standard_D4s_v5",
    resource_group: str = "test-rg",
    location: str = "eastus2",
    network_config: dict[str, Any] | None | object = _UNSET,
    ssh_public_keys: list[str] | None | object = _UNSET,
    image: dict[str, Any] | None | object = _UNSET,
    **overrides: Any,
) -> AzureTemplate:
    """Build a canonical Azure test template with overridable defaults."""
    config: dict[str, Any] = {
        "template_id": template_id,
        "provider_api": provider_api,
        "vm_size": vm_size,
        "resource_group": resource_group,
        "location": location,
        "network_config": (
            {"subnet_id": "/subscriptions/.../subnets/default"}
            if network_config is _UNSET
            else network_config
        ),
        "ssh_public_keys": (
            ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"]
            if ssh_public_keys is _UNSET
            else ssh_public_keys
        ),
        "image": (
            {
            "publisher": "Canonical",
            "offer": "0001-com-ubuntu-server-jammy",
            "sku": "22_04-lts-gen2",
            "version": "latest",
            }
            if image is _UNSET
            else image
        ),
    }
    config.update(overrides)
    return AzureTemplate(**config)


def make_single_vm_azure_client() -> MagicMock:
    """Build the SingleVM handler's async-over-sync Azure client double."""
    azure_client = MagicMock()
    async_compute = MagicMock()
    async_resource = MagicMock()

    async_compute.virtual_machines.get = AsyncMock(
        side_effect=azure_client.compute_client.virtual_machines.get
    )
    async_compute.virtual_machines.begin_delete = AsyncMock(
        side_effect=azure_client.compute_client.virtual_machines.begin_delete
    )
    async_compute.virtual_machines.list = MagicMock(
        side_effect=lambda *args, **kwargs: AsyncPager(
            azure_client.compute_client.virtual_machines.list(*args, **kwargs)
        )
    )
    async_compute.ssh_public_keys.get = AsyncMock(
        side_effect=azure_client.compute_client.ssh_public_keys.get
    )
    async_resource.resources.begin_create_or_update = AsyncMock(
        side_effect=azure_client.resource_client.resources.begin_create_or_update
    )
    async_resource.resources.get = AsyncMock(
        side_effect=azure_client.resource_client.resources.get
    )

    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    azure_client.get_async_resource_client = AsyncMock(return_value=async_resource)
    return azure_client


def make_vmss_azure_client() -> MagicMock:
    """Build the VMSS handler's async-over-sync Azure client double."""
    azure_client = MagicMock()
    async_compute = MagicMock()

    async_compute.virtual_machine_scale_sets.begin_create_or_update = AsyncMock(
        side_effect=azure_client.compute_client.virtual_machine_scale_sets.begin_create_or_update
    )
    async_compute.virtual_machine_scale_sets.get = AsyncMock(
        side_effect=azure_client.compute_client.virtual_machine_scale_sets.get
    )
    async_compute.virtual_machine_scale_sets.begin_delete_instances = AsyncMock(
        side_effect=azure_client.compute_client.virtual_machine_scale_sets.begin_delete_instances
    )
    async_compute.virtual_machine_scale_sets.begin_delete = AsyncMock(
        side_effect=azure_client.compute_client.virtual_machine_scale_sets.begin_delete
    )
    async_compute.virtual_machine_scale_set_vms.list = MagicMock(
        side_effect=lambda *args, **kwargs: AsyncPager(
            azure_client.compute_client.virtual_machine_scale_set_vms.list(*args, **kwargs)
        )
    )
    async_compute.virtual_machines.list = MagicMock(
        side_effect=lambda *args, **kwargs: AsyncPager(
            azure_client.compute_client.virtual_machines.list(*args, **kwargs)
        )
    )
    async_compute.virtual_machines.begin_delete = AsyncMock(
        side_effect=azure_client.compute_client.virtual_machines.begin_delete
    )
    async_compute.ssh_public_keys.get = AsyncMock(
        side_effect=azure_client.compute_client.ssh_public_keys.get
    )

    azure_client.get_async_compute_client = AsyncMock(return_value=async_compute)
    return azure_client


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
    )
    harness.strategy.initialize()
    return harness
