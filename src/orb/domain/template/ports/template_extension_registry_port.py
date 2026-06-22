"""Port for provider template extension registries."""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class TemplateExtensionRegistryPort(Protocol):
    """Contract for a registry that provides provider extension defaults.

    The application layer depends only on this protocol — not on the concrete
    registry implementation — keeping the application→infrastructure boundary clean.
    Implementations may delegate to class-level state (e.g. the global
    ``TemplateExtensionRegistry``) via an adapter registered in DI.
    """

    def get_extension_defaults(
        self,
        provider_type: str,
        config_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:  # type: ignore[return]
        """Return template defaults contributed by the named provider extension.

        Args:
            provider_type: Provider type string (e.g. ``'aws'``).
            config_data: Optional per-instance configuration data that may be
                used to instantiate an extension class on demand.

        Returns:
            Mapping of template field names to default values.  Returns an
            empty dict when no extension is registered for *provider_type*.
        """
        ...
