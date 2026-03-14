"""Infrastructure events package - CQRS-aligned event system."""

from orb.infrastructure.events.infrastructure_events import (
    OperationCompletedEvent,
    OperationFailedEvent,
    OperationStartedEvent,
    ResourceCreatedEvent,
    ResourceDeletedEvent,
    ResourceErrorEvent,
    ResourceEvent,
    ResourcesCleanedEvent,
    ResourceUpdatedEvent,
)
from orb.infrastructure.events.publisher import (
    ConfigurableEventPublisher,
    create_event_publisher,
)

# Import storage events (infrastructure monitoring)
from orb.infrastructure.events.storage_events import (
    ConnectionPoolEvent,
    RepositoryOperationCompletedEvent,
    RepositoryOperationFailedEvent,
    RepositoryOperationStartedEvent,
    SlowQueryDetectedEvent,
    StorageEvent,
    StorageHealthCheckEvent,
    StoragePerformanceEvent,
    StorageStrategyEvent,
    StorageStrategyFailoverEvent,
    StorageStrategySelectedEvent,
    TransactionCommittedEvent,
    TransactionStartedEvent,
)
from orb.infrastructure.events.system_events import (
    ApplicationErrorEvent,
    ApplicationShutdownEvent,
    ApplicationStartedEvent,
    AuditTrailEvent,
    ComplianceEvent,
    ConfigurationChangedEvent,
    ConfigurationErrorEvent,
    ConfigurationLoadedEvent,
    HealthCheckEvent,
    PerformanceMetricEvent,
    SecurityEvent,
    SystemEvent,
)

# Import new EventBus system
try:
    from orb.application.events import EventBus, create_event_bus

    _NEW_EVENT_SYSTEM_AVAILABLE = True
except ImportError:
    _NEW_EVENT_SYSTEM_AVAILABLE = False
    EventBus = None
    create_event_bus = None


def get_event_publisher() -> ConfigurableEventPublisher:
    """Get event publisher instance from DI container (legacy)."""
    from orb.infrastructure.di.container import get_container

    container = get_container()
    return container.get(ConfigurableEventPublisher)


def get_event_bus():
    """
    Get EventBus instance from DI container (new CQRS-aligned system).

    This is the preferred way to get event handling in the new architecture.
    Falls back to legacy publisher if new system isn't available.
    """
    if not _NEW_EVENT_SYSTEM_AVAILABLE:
        # Fallback to legacy system
        return get_event_publisher()

    try:
        from orb.infrastructure.di.container import get_container
        from orb.infrastructure.logging.logger import get_logger

        container = get_container()

        # Try to get EventBus from container
        event_bus = container.get_optional(EventBus)  # type: ignore[arg-type]
        if event_bus is not None:
            return event_bus

        # EventBus not in container — warn so the misconfiguration is visible
        logger = get_logger(__name__)
        logger.warning(
            "EventBus not registered in DI container; creating a transient instance. "
            "Events published to this instance will not reach subscribers."
        )
        return create_event_bus(logger)  # type: ignore[misc]
    except Exception as e:
        # Final fallback to legacy system
        from orb.infrastructure.logging.logger import get_logger

        get_logger(__name__).warning(
            "EventBus unavailable, falling back to legacy publisher: %s", e
        )
        return get_event_publisher()


__all__: list[str] = [
    # Infrastructure events
    "ApplicationErrorEvent",
    "ApplicationShutdownEvent",
    "ApplicationStartedEvent",
    "AuditTrailEvent",
    "ComplianceEvent",
    "ConfigurableEventPublisher",
    "ConfigurationChangedEvent",
    "ConfigurationErrorEvent",
    "ConfigurationLoadedEvent",
    "ConnectionPoolEvent",
    "EventBus",
    "HealthCheckEvent",
    "OperationCompletedEvent",
    "OperationFailedEvent",
    "OperationStartedEvent",
    "PerformanceMetricEvent",
    "RepositoryOperationCompletedEvent",
    "RepositoryOperationFailedEvent",
    "RepositoryOperationStartedEvent",
    "ResourceCreatedEvent",
    "ResourceDeletedEvent",
    "ResourceErrorEvent",
    "ResourceEvent",
    "ResourceUpdatedEvent",
    "ResourcesCleanedEvent",
    "SecurityEvent",
    "SlowQueryDetectedEvent",
    "StorageEvent",
    "StorageHealthCheckEvent",
    "StoragePerformanceEvent",
    "StorageStrategyEvent",
    "StorageStrategyFailoverEvent",
    "StorageStrategySelectedEvent",
    "SystemEvent",
    "TransactionCommittedEvent",
    "TransactionStartedEvent",
    "create_event_bus",
    "create_event_publisher",
    "get_event_bus",
    "get_event_publisher",
]
