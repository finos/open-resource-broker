"""Adapter to bridge ProviderContext with ProviderPort interface."""

from typing import Any, Dict, List

from src.domain.base.ports.provider_port import ProviderPort
from src.domain.machine.aggregate import Machine
from src.domain.request.aggregate import Request
from src.domain.template.aggregate import Template
from src.providers.base.strategy.provider_context import ProviderContext


class ProviderContextAdapter(ProviderPort):
    """Adapter that wraps ProviderContext to implement ProviderPort interface."""

    def __init__(self, provider_context: ProviderContext):
        """Initialize adapter with existing ProviderContext."""
        self.provider_context = provider_context

    def provision_resources(self, request: Request) -> List[Machine]:
        """Provision resources using existing ProviderContext."""
        # This would need to be implemented based on existing ProviderContext methods
        # For now, return empty list to maintain interface compliance
        return []

    def terminate_resources(self, machine_ids: List[str]) -> None:
        """Terminate resources using existing ProviderContext."""
        # Implementation would delegate to ProviderContext

    def get_available_templates(self) -> List[Template]:
        """Get available templates using existing ProviderContext."""
        # Implementation would delegate to ProviderContext
        return []

    def validate_template(self, template: Template) -> bool:
        """Validate template using existing ProviderContext."""
        # Implementation would delegate to ProviderContext
        return True

    def get_resource_status(self, machine_ids: List[str]) -> Dict[str, Any]:
        """Get resource status using existing ProviderContext."""
        # Implementation would delegate to ProviderContext
        return {}

    def get_provider_info(self) -> Dict[str, Any]:
        """Get provider information using existing ProviderContext."""
        return {
            "type": "ProviderContextAdapter",
            "strategies": (
                list(self.provider_context.strategy.keys())
                if hasattr(self.provider_context, "strategies")
                else []
            ),
        }
