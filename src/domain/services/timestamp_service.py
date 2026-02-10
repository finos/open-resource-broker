"""Domain service for timestamp operations."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Union


class TimestampService(ABC):
    """Domain service for timestamp formatting operations."""
    
    @abstractmethod
    def format_for_display(self, timestamp: Union[datetime, float, int, None]) -> str | None:
        """Format timestamp for user display."""
        pass
    
    @abstractmethod
    def current_timestamp(self) -> str:
        """Get current timestamp formatted for display."""
        pass
