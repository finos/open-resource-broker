"""Azure template catalog loading."""

from __future__ import annotations

from typing import Any, Protocol, cast

from orb.domain.base.ports import LoggingPort
from orb.providers.azure.domain.template.value_objects import AzureProviderApi


class SchedulerTemplateStrategy(Protocol):
    """Structural subset used from the active scheduler strategy."""

    def get_template_paths(self) -> list[str]:
        """Return configured template search paths."""
        ...

    def load_templates_from_path(self, path: str) -> list[dict[str, Any]]:
        """Load templates from one scheduler-managed path."""
        ...


class AzureTemplateCatalogService:
    """Load Azure templates from the active scheduler or a local fallback catalog."""

    def __init__(self, logger: LoggingPort) -> None:
        self._logger = logger

    def get_available_templates(self) -> list[dict[str, Any]]:
        """Load templates from the active scheduler, falling back to built-in defaults."""
        try:
            from orb.infrastructure.scheduler.registry import get_scheduler_registry

            scheduler_registry = get_scheduler_registry()
            # TODO: SchedulerRegistry.get_active_strategy() does not exist; this
            # call raises AttributeError at runtime, which the outer try/except
            # below swallows so we always fall back to hardcoded templates.
            # Either implement get_active_strategy() on SchedulerRegistry (the
            # registry advertises SINGLE_CHOICE mode but exposes no accessor for
            # the active type's strategy instance) or delete this code path and
            # call get_fallback_templates() directly. Same bug at
            # aws/services/template_validation_service.py:34. See
            # provider-quality-comparison.md defect #54.
            scheduler_strategy = cast(
                SchedulerTemplateStrategy | None,
                scheduler_registry.get_active_strategy(),  # type: ignore[attr-defined]
            )

            if scheduler_strategy:
                templates: list[dict[str, Any]] = []
                for path in scheduler_strategy.get_template_paths():
                    try:
                        templates.extend(scheduler_strategy.load_templates_from_path(path))
                    except Exception as exc:
                        self._logger.warning(
                            "Failed to load templates from %s: %s", path, exc, exc_info=True
                        )
                return templates

            self._logger.warning("No scheduler strategy available, using fallback templates")
            return self.get_fallback_templates()
        except Exception as exc:
            self._logger.error("Failed to load templates via scheduler strategy: %s", exc)
            return self.get_fallback_templates()

    @staticmethod
    def get_fallback_templates() -> list[dict[str, Any]]:
        """Return hard-coded sample templates used when no scheduler is available."""
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
