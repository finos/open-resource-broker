"""Infrastructure registry patterns."""

from .provider_registry import ProviderRegistry
from .storage_registry import StorageRegistry

__all__: list[str] = ["ProviderRegistry", "StorageRegistry"]
