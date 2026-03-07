"""Infrastructure implementation of timestamp service."""

from datetime import datetime, timezone
from typing import Union

from orb.domain.services.timestamp_service import TimestampService


class ISOTimestampService(TimestampService):
    """ISO format timestamp service implementation."""

    def format_for_display(self, timestamp: Union[datetime, float, int, None]) -> str | None:
        """Format timestamp to ISO format with Z timezone indicator."""
        if timestamp is None:
            return None

        if isinstance(timestamp, (int, float)):
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        elif isinstance(timestamp, datetime):
            if timestamp.tzinfo is None:
                dt = timestamp.replace(tzinfo=timezone.utc)
            else:
                dt = timestamp.astimezone(timezone.utc)
        else:
            return None

        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def format_for_dto(self, timestamp: Union[datetime, float, int, None]) -> int | None:
        """Format timestamp to unix timestamp for DTO backward compatibility."""
        if timestamp is None:
            return None

        if isinstance(timestamp, (int, float)):
            return int(timestamp)
        elif isinstance(timestamp, datetime):
            return int(timestamp.timestamp())
        else:
            return None

    def current_timestamp(self) -> str:
        """Get current timestamp in ISO format with Z timezone indicator."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def format_with_type(
        self, timestamp: Union[datetime, float, int, None], format_type: str
    ) -> int | str | None:
        """Format timestamp based on requested format type."""
        if format_type == "unix":
            return self.format_for_dto(timestamp)
        elif format_type == "iso":
            return self.format_for_display(timestamp)
        else:  # auto
            return self.format_for_dto(timestamp)
