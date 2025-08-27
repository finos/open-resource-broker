"""Persistence events - Repository and storage monitoring."""

from datetime import datetime
from typing import Any, Optional

from pydantic import Field

from .base_events import (
    ErrorEvent,
    InfrastructureEvent,
    OperationEvent,
    PerformanceEvent,
    TimedEvent,
)

# =============================================================================
# REPOSITORY OPERATION EVENTS
# =============================================================================


class PersistenceEvent(InfrastructureEvent):
    """Base class for persistence-related events."""

    operation_id: str
    entity_type: str
    entity_id: str
    storage_strategy: str


class RepositoryOperationStartedEvent(PersistenceEvent):
    """Event raised when a repository operation starts."""

    operation_type: str  # "save", "find", "delete", "update"
    start_time: datetime = Field(default_factory=datetime.utcnow)


class RepositoryOperationCompletedEvent(PersistenceEvent, OperationEvent):
    """Event raised when a repository operation completes successfully."""

    records_affected: int = 1


class RepositoryOperationFailedEvent(PersistenceEvent, ErrorEvent, TimedEvent):
    """Event raised when a repository operation fails."""

    operation_type: str
    duration_ms: Optional[float] = None


class SlowQueryDetectedEvent(PersistenceEvent, PerformanceEvent):
    """Event raised when a slow repository operation is detected."""

    operation_type: str
    query_details: dict[str, Any] = Field(default_factory=dict)


class TransactionStartedEvent(InfrastructureEvent):
    """Event raised when a transaction starts."""

    transaction_id: str
    isolation_level: str = "default"
    entities_involved: list[str] = Field(default_factory=list)


class TransactionCommittedEvent(InfrastructureEvent, TimedEvent):
    """Event raised when a transaction is committed."""

    transaction_id: str
    entities_affected: list[str] = Field(default_factory=list)
    operations_count: int


# =============================================================================
# STORAGE STRATEGY EVENTS
# =============================================================================


class StorageEvent(InfrastructureEvent):
    """Base class for storage strategy events."""

    strategy_type: str
    entity_type: str


class StorageStrategySelectedEvent(StorageEvent):
    """Event raised when a storage strategy is selected."""

    selected_strategy: str  # "JSON", "SQL", "DynamoDB"
    selection_reason: str  # "configuration", "fallback", "performance"
    available_strategies: list[str] = Field(default_factory=list)


class StorageStrategyFailoverEvent(StorageEvent, ErrorEvent):
    """Event raised when storage strategy failover occurs."""

    from_strategy: str
    to_strategy: str
    failure_reason: str
    failover_time: datetime = Field(default_factory=datetime.utcnow)


class ConnectionPoolEvent(InfrastructureEvent):
    """Event raised for connection pool operations."""

    pool_type: str  # "SQL", "DynamoDB", "Redis"
    event_type: str  # "connection_acquired", "connection_released", "pool_exhausted"
    active_connections: int
    pool_size: int
    wait_time_ms: Optional[float] = None


class StoragePerformanceEvent(StorageEvent, PerformanceEvent):
    """Event raised for storage performance monitoring."""

    operation_type: str
    data_size_bytes: int
    throughput_ops_per_sec: Optional[float] = None


class StorageHealthCheckEvent(StorageEvent, PerformanceEvent):
    """Event raised during storage health checks."""

    health_status: str  # "healthy", "degraded", "unhealthy"
    response_time_ms: float
    error_rate_percent: float
    check_details: dict[str, Any] = Field(default_factory=dict)
