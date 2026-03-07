"""Provider-agnostic image resolution cache interface."""

from abc import ABC, abstractmethod
from typing import Optional


class ImageCache(ABC):
    """Provider-agnostic image resolution cache interface."""

    @abstractmethod
    def get(self, image_specification: str) -> Optional[str]:
        """Get cached image ID for specification."""
        pass

    @abstractmethod
    def set(self, image_specification: str, image_id: str) -> None:
        """Cache resolved image ID."""
        pass

    @abstractmethod
    def clear_expired(self) -> None:
        """Remove expired cache entries."""
        pass
