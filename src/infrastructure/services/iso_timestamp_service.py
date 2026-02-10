"""Infrastructure implementation of timestamp service."""

from datetime import datetime, timezone
from typing import Union

from domain.services.timestamp_service import TimestampService


class ISOTimestampService(TimestampService):
    """ISO format timestamp service implementation."""
    
    def format_for_display(self, timestamp: Union[datetime, float, int, None]) -> str | None:
        """Format timestamp to ISO format with UTC timezone."""
        if timestamp is None:
            return None
            
        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        elif isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                dt = timestamp.replace(tzinfo=timezone.utc)
            else:
                dt = timestamp
        else:
            return None
            
        return dt.isoformat()
    
    def current_timestamp(self) -> str:
        """Get current timestamp in ISO format with UTC timezone."""
        return datetime.now(timezone.utc).isoformat()
