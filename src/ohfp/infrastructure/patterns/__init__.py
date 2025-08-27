"""Infrastructure patterns package."""

from infrastructure.patterns.singleton_access import get_singleton
from infrastructure.patterns.singleton_registry import SingletonRegistry

__all__: list[str] = ["SingletonRegistry", "get_singleton"]
