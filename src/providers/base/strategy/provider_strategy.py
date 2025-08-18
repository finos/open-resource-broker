"""Provider Strategy Pattern - Core strategy interface and value objects.

This module implements the Strategy pattern for provider operations, allowing
runtime selection and switching of provider strategies while maintaining
clean separation of concerns and SOLID principles compliance.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict

from infrastructure.interfaces.provider import BaseProviderConfig


class ProviderOperationType(str, Enum):
    """Types of provider operations that can be executed via strategy pattern."""

    CREATE_INSTANCES = "create_instances"
    TERMINATE_INSTANCES = "terminate_instances"
    GET_INSTANCE_STATUS = "get_instance_status"
    DESCRIBE_RESOURCE_INSTANCES = "describe_resource_instances"
    VALIDATE_TEMPLATE = "validate_template"
    GET_AVAILABLE_TEMPLATES = "get_available_templates"
    HEALTH_CHECK = "health_check"


@dataclass
class ProviderOperation:
    """Value object representing a provider operation to be executed."""

    operation_type: ProviderOperationType
    parameters: Dict[str, Any]
    context: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        """Validate operation parameters after initialization."""
        if not isinstance(self.parameters, dict):
            raise ValueError("Operation parameters must be a dictionary") from e

        if self.context is not None and not isinstance(self.context, dict):
            raise ValueError("Operation context must be a dictionary or None") from e


class ProviderResult(BaseModel):
    """Value object representing the result of a provider operation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool
    data: Any = None
    error_message: Optional[str] = None
    error_code: Optional[str] = None
    metadata: Dict[str, Any] = {}

    @classmethod
    def success_result(cls, data: Any = None, metadata: Dict[str, Any] = None) -> "ProviderResult":
        """Create a successful result."""
        return cls(success=True, data=data, metadata=metadata or {})

    @classmethod
    def error_result(
        cls, error_message: str, error_code: str = None, metadata: Dict[str, Any] = None
    ) -> "ProviderResult":
        """Create an error result."""
        return cls(
            success=False,
            error_message=error_message,
            error_code=error_code,
            metadata=metadata or {},
        )


class ProviderCapabilities(BaseModel):
    """Value object representing provider capabilities and features."""

    provider_type: str
    supported_operations: List[ProviderOperationType]
    features: Dict[str, Any] = {}
    limitations: Dict[str, Any] = {}
    performance_metrics: Dict[str, Any] = {}

    def supports_operation(self, operation: ProviderOperationType) -> bool:
        """Check if provider supports a specific operation."""
        return operation in self.supported_operations

    def get_feature(self, feature_name: str, default: Any = None) -> Any:
        """Get a specific feature value."""
        return self.features.get(feature_name, default)


class ProviderHealthStatus(BaseModel):
    """Value object representing provider health status."""

    is_healthy: bool
    status_message: str
    last_check_time: Optional[str] = None
    response_time_ms: Optional[float] = None
    error_details: Optional[Dict[str, Any]] = None

    @classmethod
    def healthy(
        cls, message: str = "Provider is healthy", response_time_ms: float = None
    ) -> "ProviderHealthStatus":
        """Create a healthy status."""
        return cls(is_healthy=True, status_message=message, response_time_ms=response_time_ms)

    @classmethod
    def unhealthy(
        cls, message: str, error_details: Dict[str, Any] = None
    ) -> "ProviderHealthStatus":
        """Create an unhealthy status."""
        return cls(is_healthy=False, status_message=message, error_details=error_details or {})


class ProviderStrategy(ABC):
    """
    Abstract base class for provider strategies.

    This interface defines the contract that all provider strategies must implement.
    It follows the Strategy pattern to allow runtime selection and switching of
    provider implementations while maintaining clean separation of concerns.

    The strategy pattern enables:
    - Runtime provider switching
    - Provider composition and chaining
    - Fallback and resilience strategies
    - Load balancing across providers
    - Easy testing and mocking
    """

    def __init__(self, config: BaseProviderConfig) -> None:
        """
        Initialize the provider strategy with configuration.

        Args:
            config: Provider-specific configuration

        Raises:
            ValueError: If configuration is invalid
        """
        self._config = config
        self._initialized = False

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """
        Get the provider type identifier.

        Returns:
            String identifier for the provider type (e.g., 'aws', 'provider1', 'provider2')
        """

    @property
    def is_initialized(self) -> bool:
        """Check if the strategy is initialized."""
        return self._initialized

    @abstractmethod
    def initialize(self) -> bool:
        """
        Initialize the provider strategy.

        This method should set up any necessary connections, validate configuration,
        and prepare the strategy for operation execution.

        Returns:
            True if initialization successful, False otherwise

        Raises:
            ProviderError: If initialization fails critically
        """

    @abstractmethod
    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        """
        Execute a provider operation using this strategy.

        This is the core method of the strategy pattern that executes
        provider-specific operations based on the operation type and parameters.

        Args:
            operation: The operation to execute

        Returns:
            Result of the operation execution

        Raises:
            ProviderError: If operation execution fails
            ValueError: If operation is not supported
        """

    async def execute_operation_async(self, operation: ProviderOperation) -> ProviderResult:
        """
        Execute a provider operation asynchronously.

        Default implementation runs sync version in thread pool.
        Subclasses can override for native async implementation.

        Args:
            operation: The operation to execute

        Returns:
            Result of the operation execution
        """
        import asyncio
        import concurrent.futures

        # Run sync version in thread pool to avoid blocking event loop
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, self.execute_operation, operation)

    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        """
        Get the capabilities and features of this provider strategy.

        Returns:
            Provider capabilities including supported operations and features
        """

    @abstractmethod
    def check_health(self) -> ProviderHealthStatus:
        """
        Check the health status of this provider strategy.

        This method should verify that the provider is operational and
        can handle requests. It's used for health monitoring and
        strategy selection decisions.

        Returns:
            Current health status of the provider
        """

    @abstractmethod
    def cleanup(self) -> None:
        """
        Clean up resources used by the strategy.

        This method should be called when the strategy is no longer needed
        to ensure resource cleanup (connections, handles, etc.).
        Default implementation does nothing - override if cleanup is needed.
        """

    def __enter__(self) -> "ProviderStrategy":
        """Context manager entry."""
        if not self._initialized and not self.initialize():
            raise RuntimeError(
                f"Failed to initialize {self.provider_type} provider strategy"
            ) from e
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit with cleanup."""
        self.cleanup()
