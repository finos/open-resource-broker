"""Protocol for per-provider defaults loaders."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ProviderDefaultsLoaderPort(Protocol):
    """Contract for a loader that returns provider-contributed config defaults.

    Implementations read a provider's bundled defaults file (or any other
    source) and return a raw config dict in the same shape as
    ``default_config.json``.  The loader is registered once per provider type
    in ``DefaultsLoaderRegistry`` so that ``ConfigurationLoader`` can iterate
    all registered loaders without knowing concrete provider classes.
    """

    def load_defaults(self) -> dict:  # type: ignore[return]
        """Return the provider's contributed config defaults.

        Returns:
            Raw configuration dictionary (same shape as ``default_config.json``).
            Return an empty dict if the provider has no defaults to contribute.
        """
        ...
