"""Azure template catalog loading."""

from __future__ import annotations

from typing import Any

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureProviderApi


class AzureTemplateCatalogService:
    """Load Azure templates from the active scheduler or a local fallback catalog."""

    def __init__(self, logger: LoggingPort) -> None:
        self._logger = logger

    def get_available_templates(self) -> list[dict[str, Any]]:
        try:
            from orb.infrastructure.scheduler.registry import get_scheduler_registry

            scheduler_registry = get_scheduler_registry()
            scheduler_strategy = scheduler_registry.get_active_strategy()

            if scheduler_strategy:
                templates: list[dict[str, Any]] = []
                for path in scheduler_strategy.get_template_paths():
                    try:
                        templates.extend(scheduler_strategy.load_templates_from_path(path))
                    except Exception as exc:
                        self._logger.warning("Failed to load templates from %s: %s", path, exc)
                return templates

            self._logger.warning("No scheduler strategy available, using fallback templates")
            return self.get_fallback_templates()
        except Exception as exc:
            self._logger.error("Failed to load templates via scheduler strategy: %s", exc)
            return self.get_fallback_templates()

    @staticmethod
    def get_fallback_templates() -> list[dict[str, Any]]:
        return [
            {
                "template_id": "azure-vmss-linux-basic",
                "name": "Azure VMSS Linux Basic",
                "description": "VMSS with Ubuntu 22.04 LTS on Standard_D4s_v5",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.VMSS.value,
                "vm_size": "Standard_D4s_v5",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "ssh_key_name": "my-azure-ssh-key",
                "image": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                "max_instances": 2,
            },
            {
                "template_id": "azure-vmss-spot",
                "name": "Azure VMSS Spot Instances",
                "description": "VMSS with Spot VMs for cost-effective workloads",
                "provider_type": "azure",
                "provider_api": AzureProviderApi.VMSS.value,
                "vm_size": "Standard_D4s_v5",
                "resource_group": "my-resource-group",
                "location": "eastus2",
                "ssh_key_name": "my-azure-ssh-key",
                "priority": "Spot",
                "eviction_policy": "Deallocate",
                "billing_profile_max_price": -1.0,
                "image": {
                    "publisher": "Canonical",
                    "offer": "0001-com-ubuntu-server-jammy",
                    "sku": "22_04-lts-gen2",
                    "version": "latest",
                },
                "max_instances": 5,
            },
        ]
