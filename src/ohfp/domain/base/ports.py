"""Domain ports - interfaces for external dependencies."""

from typing import Any, Optional, Protocol


class ConfigurationPort(Protocol):
    """Port for accessing configuration from domain layer."""

    def get_naming_config(self) -> dict[str, Any]:
        """Get naming configuration."""
        ...

    def get_validation_config(self) -> dict[str, Any]:
        """Get validation configuration."""
        ...

    def get_provider_config(self, provider_type: str) -> dict[str, Any]:
        """Get provider-specific configuration."""
        ...


class LoggingPort(Protocol):
    """Port for logging from domain layer."""

    def log_domain_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Log domain events."""
        ...

    def log_business_rule_violation(self, rule: str, context: dict[str, Any]) -> None:
        """Log business rule violations."""
        ...


class EventPublishingPort(Protocol):
    """Port for publishing domain events."""

    def publish_event(self, event: Any) -> None:
        """Publish a domain event."""
        ...

    def publish_events(self, events: list) -> None:
        """Publish multiple domain events."""
        ...


class PersistencePort(Protocol):
    """Port for persistence operations from domain layer."""

    def save_aggregate(self, aggregate: Any) -> None:
        """Save an aggregate root."""
        ...

    def find_aggregate_by_id(self, aggregate_id: str, aggregate_type: type) -> Optional[Any]:
        """Find aggregate by ID."""
        ...

    def delete_aggregate(self, aggregate_id: str, aggregate_type: type) -> None:
        """Delete aggregate by ID."""
        ...


class NotificationPort(Protocol):
    """Port for sending notifications from domain layer."""

    def send_notification(self, recipient: str, message: str, notification_type: str) -> None:
        """Send a notification."""
        ...

    def send_alert(self, alert_type: str, data: dict[str, Any]) -> None:
        """Send an alert."""
        ...
