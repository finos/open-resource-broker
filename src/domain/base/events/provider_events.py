"""Provider-agnostic domain events."""

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from pydantic import Field

from domain.base.events.base_events import DomainEvent
from domain.base.provider_interfaces import ProviderInstanceState, ProviderType


class ProviderOperationEvent(DomainEvent):
    """Event raised for provider operations."""

    provider_type: ProviderType
    operation_type: str  # e.g., "create_instance", "terminate_instance"
    provider_resource_type: str  # e.g., "instance", "volume"
    provider_resource_id: Optional[str] = Field(default=None)
    operation_status: str = Field(default="started")  # started, completed, failed
    error_message: Optional[str] = Field(default=None)

    def model_post_init(self, __context: Any) -> None:
        """Initialize aggregate information after model creation."""
        # Set the base class fields
        object.__setattr__(self, "aggregate_id", self.provider_resource_id or str(uuid4()))
        object.__setattr__(self, "aggregate_type", f"{self.provider_type.value}_resource")
        super().model_post_init(__context)
        if not self.operation_type:
            raise ValueError("Operation type cannot be empty")
        if not self.provider_resource_type:
            raise ValueError("Resource type cannot be empty")


@dataclass(frozen=True)
class ProviderRateLimitEvent(DomainEvent):
    """Event raised when provider rate limiting occurs."""

    provider_type: ProviderType
    service_name: str
    operation_name: str
    retry_after: Optional[int] = field(default=None)  # seconds

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregate_id", f"{self.provider_type.value}_{self.service_name}")
        object.__setattr__(self, "aggregate_type", f"{self.provider_type.value}_service")
        super().__post_init__()
        if not self.service_name:
            raise ValueError("Service name cannot be empty")
        if not self.operation_name:
            raise ValueError("Operation name cannot be empty")


@dataclass(frozen=True)
class ProviderCredentialsEvent(DomainEvent):
    """Event raised for provider credentials operations."""

    provider_type: ProviderType
    credential_type: str  # e.g., "access_key", "service_account", "managed_identity"
    operation: str  # e.g., "refresh", "validate", "expire"
    status: str  # success, failure, warning
    message: Optional[str] = field(default=None)

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregate_id", f"{self.provider_type.value}_credentials")
        object.__setattr__(self, "aggregate_type", f"{self.provider_type.value}_auth")
        super().__post_init__()
        if not self.credential_type:
            raise ValueError("Credential type cannot be empty")
        if not self.operation:
            raise ValueError("Operation cannot be empty")
        if not self.status:
            raise ValueError("Status cannot be empty")


@dataclass(frozen=True)
class ProviderResourceStateChangedEvent(DomainEvent):
    """Event raised when provider resource state changes."""

    provider_type: ProviderType
    resource_type: str
    resource_id: str
    previous_state: Optional[ProviderInstanceState]
    new_state: ProviderInstanceState
    state_reason: Optional[str] = field(default=None)

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregate_id", self.resource_id)
        object.__setattr__(
            self, "aggregate_type", f"{self.provider_type.value}_{self.resource_type}"
        )
        super().__post_init__()
        if not self.resource_type:
            raise ValueError("Resource type cannot be empty")
        if not self.resource_id:
            raise ValueError("Resource ID cannot be empty")


@dataclass(frozen=True)
class ProviderConfigurationEvent(DomainEvent):
    """Event raised for provider configuration changes."""

    provider_type: ProviderType
    configuration_type: str  # e.g., "region", "endpoint", "timeout"
    old_value: Optional[str] = field(default=None)
    new_value: Optional[str] = field(default=None)
    operation: str = field(default="update")  # update, validate, reset

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregate_id", f"{self.provider_type.value}_config")
        object.__setattr__(self, "aggregate_type", f"{self.provider_type.value}_configuration")
        super().__post_init__()
        if not self.configuration_type:
            raise ValueError("Configuration type cannot be empty")


@dataclass(frozen=True)
class ProviderHealthCheckEvent(DomainEvent):
    """Event raised for provider health checks."""

    provider_type: ProviderType
    service_name: str
    health_status: str  # healthy, unhealthy, degraded
    response_time_ms: Optional[int] = field(default=None)
    error_message: Optional[str] = field(default=None)

    def __post_init__(self) -> None:
        object.__setattr__(self, "aggregate_id", f"{self.provider_type.value}_{self.service_name}")
        object.__setattr__(self, "aggregate_type", f"{self.provider_type.value}_health")
        super().__post_init__()
        if not self.service_name:
            raise ValueError("Service name cannot be empty")
        if not self.health_status:
            raise ValueError("Health status cannot be empty")


class ProviderStrategySelectedEvent(DomainEvent):
    """Event raised when a provider strategy is selected for an operation."""

    strategy_name: str
    operation_type: str  # ProviderOperationType value
    selection_criteria: Optional[str] = None  # JSON string of criteria
    selection_reason: Optional[str] = None
    confidence_score: Optional[float] = None

    def model_post_init(self, __context) -> None:
        """Initialize aggregate information after model creation."""
        # Set aggregate info based on strategy
        if not self.aggregate_id:
            object.__setattr__(self, "aggregate_id", f"strategy_{self.strategy_name}")
        if not self.aggregate_type:
            object.__setattr__(self, "aggregate_type", "provider_strategy")
        super().model_post_init(__context)


class ProviderOperationExecutedEvent(DomainEvent):
    """Event raised when a provider operation is executed through strategy pattern."""

    operation_type: str  # ProviderOperationType value
    strategy_name: str
    success: bool
    execution_time_ms: float
    error_message: Optional[str] = None
    error_code: Optional[str] = None

    def model_post_init(self, __context) -> None:
        """Initialize aggregate information after model creation."""
        # Set aggregate info based on operation
        if not self.aggregate_id:
            object.__setattr__(
                self,
                "aggregate_id",
                f"operation_{self.operation_type}_{int(self.execution_time_ms)}",
            )
        if not self.aggregate_type:
            object.__setattr__(self, "aggregate_type", "provider_operation")
        super().model_post_init(__context)


class ProviderHealthChangedEvent(DomainEvent):
    """Event raised when provider health status changes."""

    provider_name: str
    old_status: Optional[str] = None  # JSON string of old ProviderHealthStatus
    new_status: str = ""  # JSON string of new ProviderHealthStatus
    source: str = "system"

    def model_post_init(self, __context) -> None:
        """Initialize aggregate information after model creation."""
        # Set aggregate info based on provider
        if not self.aggregate_id:
            object.__setattr__(self, "aggregate_id", f"health_{self.provider_name}")
        if not self.aggregate_type:
            object.__setattr__(self, "aggregate_type", "provider_health")
        super().model_post_init(__context)


class ProviderStrategyRegisteredEvent(DomainEvent):
    """Event raised when a new provider strategy is registered."""

    strategy_name: str
    provider_type: str
    capabilities: Optional[str] = None  # JSON string of capabilities
    priority: int = 0

    def model_post_init(self, __context) -> None:
        """Initialize aggregate information after model creation."""
        # Set aggregate info based on registration
        if not self.aggregate_id:
            object.__setattr__(self, "aggregate_id", f"registration_{self.strategy_name}")
        if not self.aggregate_type:
            object.__setattr__(self, "aggregate_type", "provider_registration")
        super().model_post_init(__context)


# Export all provider events
__all__: list[str] = [
    "ProviderOperationEvent",
    "ProviderRateLimitEvent",
    "ProviderCredentialsEvent",
    "ProviderResourceStateChangedEvent",
    "ProviderConfigurationEvent",
    "ProviderHealthCheckEvent",
    # Provider Strategy Events
    "ProviderStrategySelectedEvent",
    "ProviderOperationExecutedEvent",
    "ProviderHealthChangedEvent",
    "ProviderStrategyRegisteredEvent",
]
