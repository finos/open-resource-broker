"""CLI spec registry — maps provider type strings to ProviderCLISpecPort instances."""

from orb.infrastructure.registry.simple_registry import SimpleRegistry
from orb.providers.base.provider_cli_spec_port import ProviderCLISpecPort


class CLISpecRegistry(SimpleRegistry[ProviderCLISpecPort]):
    """Registry mapping provider type strings to ProviderCLISpecPort instances.

    Use ``get_or_none`` when absence is acceptable (e.g. iterating all
    providers to build a display list and gracefully degrading when no spec is
    found).  Use ``get`` when the spec must exist — it raises
    ``RegistryLookupError`` naming the missing key and listing every registered
    key so misconfigurations are caught immediately.
    """

    _registry_name = "CLISpecRegistry"
    _store: dict[str, ProviderCLISpecPort] = {}
