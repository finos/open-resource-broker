"""Protocol defining how a provider exposes itself to the CLI.

Placed in orb.providers.base (not orb.domain) because it carries an
argparse dependency, which is a CLI framework.  Domain must remain
framework-free; providers is the correct neutral location for protocols
that bridge provider implementations with the CLI tier.
"""

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
