"""Entity serialization components for repository operations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from infrastructure.logging.logger import get_logger

T = TypeVar("T")


class EntitySerializer(ABC):
    """Base interface for entity serialization."""

    @abstractmethod
    def to_dict(self, entity: Any) -> dict[str, Any]:
        """Convert entity to dictionary."""

    @abstractmethod
    def from_dict(self, data: dict[str, Any]) -> Any:
        """Convert dictionary to entity."""


class BaseEntitySerializer(EntitySerializer):
    """Base implementation for entity serialization."""

    def __init__(self) -> None:
        """Initialize serializer."""
        self.logger = get_logger(__name__)
