"""Domain service for timestamp operations."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Union


class TimestampService(ABC):
    """Domain service for timestamp formatting operations."""

    @abstractmethod
    def format_for_display(self, timestamp: Union[datetime, float, int, None]) -> str | None:
        """Format timestamp for user display (ISO format)."""
        pass

    @abstractmethod
    def format_for_dto(self, timestamp: Union[datetime, float, int, None]) -> int | None:
        """Format timestamp for DTO (unix timestamp for backward compatibility)."""
        pass

    @abstractmethod
    def current_timestamp(self) -> str:
        """Get current timestamp formatted for display."""
        pass

    @abstractmethod
    def format_with_type(
        self, timestamp: Union[datetime, float, int, None], format_type: str
    ) -> int | str | None:
        """Format timestamp based on requested format type."""
        pass
