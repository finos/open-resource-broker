"""Infrastructure patterns package."""

from infrastructure.patterns.singleton_access import get_singleton
from infrastructure.patterns.singleton_registry import SingletonRegistry

__all__ = ["SingletonRegistry", "get_singleton"]
