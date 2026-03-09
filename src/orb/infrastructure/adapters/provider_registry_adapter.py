"""Adapter to bridge Provider Registry with ProviderPort interface."""

from typing import Any

from orb.domain.base.ports.provider_port import ProviderPort
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.providers.registry import ProviderRegistry


class ProviderRegistryAdapter(ProviderPort):
    """Adapter that wraps Provider Registry to implement ProviderPort interface."""

    def __init__(self, registry: ProviderRegistry) -> None:
        """Initialize adapter with Provider Registry."""
        self.registry = registry

    def provision_resources(self, request: Request) -> list[Machine]:
        """Provision resources using Provider Registry."""
        # This would need to be implemented based on Provider Registry methods
        # For now, return empty list to maintain interface compliance
        return []

    def terminate_resources(self, *args, **kwargs) -> None:
        """Terminate resources using Provider Registry."""
        # Implementation would delegate to Provider Registry
        pass

    def get_available_templates(self) -> list[Template]:
        """Get available templates using Provider Registry."""
        # Implementation would delegate to Provider Registry
        return []

    def validate_template(self, template: Template) -> bool:
        """Validate template using Provider Registry."""
        # Implementation would delegate to Provider Registry
        return True

    def get_resource_status(self, machine_ids: list[str]) -> dict[str, Any]:
        """Get resource status using Provider Registry."""
        # Implementation would delegate to Provider Registry
        return {}

    def available_strategies(self) -> list[str]:
        """Get available strategies from the Provider Registry."""
        return (
            self.registry.get_registered_providers()
            + self.registry.get_registered_provider_instances()
        )

    def get_provider_info(self) -> dict[str, Any]:
        """Get provider information using Provider Registry."""
        return {
            "type": "ProviderRegistryAdapter",
            "strategies": self.available_strategies(),
        }

    def get_strategy(self, strategy_name: str) -> Any:
        """Get specific provider strategy from Provider Registry."""
        try:
            if self.registry.is_provider_instance_registered(strategy_name):
                return self.registry.get_or_create_strategy(strategy_name, {})
            elif self.registry.is_provider_registered(strategy_name):
                return self.registry.get_or_create_strategy(strategy_name, {})
            return None
        except Exception:
            return None

    def discover_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Discover infrastructure by delegating to the provider strategy."""
        provider_type = provider_config.get("type", "")
        if not provider_type:
            return {}
        if not self.registry.ensure_provider_type_registered(provider_type):
            return {}
        strategy = self.registry.get_or_create_strategy(provider_type, {})
        if strategy is None or not hasattr(strategy, "discover_infrastructure"):
            return {}
        return strategy.discover_infrastructure(provider_config)

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Discover infrastructure interactively by delegating to the provider strategy."""
        provider_type = provider_config.get("type", "")
        if not provider_type:
            return {}
        if not self.registry.ensure_provider_type_registered(provider_type):
            return {}
        strategy = self.registry.get_or_create_strategy(provider_type, {})
        if strategy is None or not hasattr(strategy, "discover_infrastructure_interactive"):
            return {}
        return strategy.discover_infrastructure_interactive(provider_config)

    def validate_infrastructure(self, provider_config: dict[str, Any]) -> dict[str, Any]:
        """Validate infrastructure by delegating to the provider strategy."""
        provider_type = provider_config.get("type", "")
        if not provider_type:
            return {}
        if not self.registry.ensure_provider_type_registered(provider_type):
            return {}
        strategy = self.registry.get_or_create_strategy(provider_type, {})
        if strategy is None or not hasattr(strategy, "validate_infrastructure"):
            return {}
        return strategy.validate_infrastructure(provider_config)

    def execute_with_strategy(self, *args, **kwargs):
        """Execute with strategy using Provider Registry."""
        # Implementation would delegate to Provider Registry
        raise NotImplementedError("execute_with_strategy not available")
