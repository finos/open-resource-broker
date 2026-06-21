"""CLI spec registry — maps provider type strings to ProviderCLISpecPort instances."""

from orb.domain.base.ports.provider_cli_spec_port import ProviderCLISpecPort


class CLISpecRegistry:
    """Simple registry mapping provider type strings to ProviderCLISpecPort instances."""

    _specs: dict[str, ProviderCLISpecPort] = {}

    @classmethod
    def register(cls, provider_type: str, spec: ProviderCLISpecPort) -> None:
        """Register a CLI spec for a provider type."""
        cls._specs[provider_type] = spec

    @classmethod
    def get(cls, provider_type: str) -> ProviderCLISpecPort | None:
        """Return the CLI spec for a provider type, or None if not registered."""
        return cls._specs.get(provider_type)

    @classmethod
    def all(cls) -> dict[str, ProviderCLISpecPort]:
        """Return all registered CLI specs."""
        return dict(cls._specs)
