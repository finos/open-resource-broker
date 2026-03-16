"""Protocol and registry for provider CLI argument specifications."""

import argparse
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ProviderCLISpecPort(Protocol):
    """Protocol defining how a provider exposes itself to the CLI."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add provider-specific arguments to the given parser."""
        pass

    def extract_config(self, args: argparse.Namespace) -> dict[str, Any]:  # type: ignore[return]
        """Extract a full provider config dict from parsed args (add path)."""
        pass

    def extract_partial_config(self, args: argparse.Namespace) -> dict[str, Any]:  # type: ignore[return]
        """Extract only the fields that were explicitly provided (update path)."""
        pass

    def validate_add(self, args: argparse.Namespace) -> list[str]:  # type: ignore[return]
        """Validate args for the add command. Returns list of error messages; empty = valid."""
        pass

    def generate_name(self, args: argparse.Namespace) -> str:  # type: ignore[return]
        """Generate a provider instance name from parsed args."""
        pass

    def format_display(self, config: dict[str, Any]) -> list[tuple[str, str]]:  # type: ignore[return]
        """Return a list of (label, value) pairs for display."""
        pass


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
