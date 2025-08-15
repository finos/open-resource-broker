"""
System DTOs for system-related queries and responses.

This module provides strongly-typed DTOs for system operations,
replacing Dict[str, Any] returns with appropriate type safety.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from application.dto.base import BaseDTO


class ProviderConfigDTO(BaseDTO):
    """DTO for provider configuration information."""

    provider_mode: str = Field(description="Current provider mode (e.g., 'legacy', 'strategy')")
    active_providers: List[str] = Field(description="List of active provider names")
    provider_count: int = Field(description="Number of active providers")
    default_provider: Optional[str] = Field(None, description="Default provider name")
    configuration_source: str = Field(
        description="Source of configuration (e.g., 'file', 'environment')"
    )
    last_updated: Optional[datetime] = Field(None, description="Last configuration update time")


class ValidationResultDTO(BaseDTO):
    """DTO for provider configuration validation results."""

    is_valid: bool = Field(description="Whether the configuration is valid")
    validation_errors: List[str] = Field(
        default_factory=list, description="List of validation errors"
    )
    warnings: List[str] = Field(default_factory=list, description="List of validation warnings")


class StorageStrategyDTO(BaseDTO):
    """DTO for storage strategy information."""

    name: str = Field(description="Storage strategy name")
    active: bool = Field(description="Whether this strategy is currently active")
    registered: bool = Field(description="Whether this strategy is registered")
    description: Optional[str] = Field(None, description="Strategy description")
    capabilities: List[str] = Field(default_factory=list, description="Strategy capabilities")


class StorageStrategyListResponse(BaseDTO):
    """Response DTO for storage strategies list."""

    strategies: List[StorageStrategyDTO] = Field(description="List of available storage strategies")
    current_strategy: str = Field(description="Currently active storage strategy")
    total_count: int = Field(description="Total number of strategies")


class SchedulerStrategyDTO(BaseDTO):
    """DTO for scheduler strategy information."""

    name: str = Field(description="Scheduler strategy name")
    active: bool = Field(description="Whether this strategy is currently active")
    registered: bool = Field(description="Whether this strategy is registered")
    description: Optional[str] = Field(None, description="Strategy description")
    capabilities: List[str] = Field(default_factory=list, description="Strategy capabilities")


class SchedulerStrategyListResponse(BaseDTO):
    """Response DTO for scheduler strategies list."""

    strategies: List[SchedulerStrategyDTO] = Field(
        description="List of available scheduler strategies"
    )
    current_strategy: str = Field(description="Currently active scheduler strategy")
    total_count: int = Field(description="Total number of strategies")


class SchedulerConfigurationResponse(BaseDTO):
    """Response DTO for scheduler configuration."""

    scheduler_name: str = Field(description="Scheduler strategy name")
    configuration: Dict[str, Any] = Field(description="Scheduler configuration details")
    active: bool = Field(description="Whether this scheduler is currently active")
    valid: bool = Field(description="Whether the configuration is valid")
    found: bool = Field(description="Whether the scheduler configuration was found")


class StorageHealthResponse(BaseDTO):
    """Response DTO for storage health status."""

    strategy_name: str = Field(description="Storage strategy name")
    healthy: bool = Field(description="Whether storage is healthy")
    status: str = Field(description="Storage status description")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional health details")


class StorageMetricsResponse(BaseDTO):
    """Response DTO for storage performance metrics."""

    strategy_name: str = Field(description="Storage strategy name")
    time_range: str = Field(description="Time range for metrics")
    operations_count: int = Field(description="Total number of operations")
    average_latency: float = Field(description="Average operation latency in ms")
    error_rate: float = Field(description="Error rate as percentage")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional metric details")


class ConfigurationValueResponse(BaseDTO):
    """Response DTO for configuration value queries."""

    key: str = Field(description="Configuration key")
    value: Any = Field(description="Configuration value")
    section: Optional[str] = Field(None, description="Configuration section")
    found: bool = Field(description="Whether the configuration key was found")


class ConfigurationSectionResponse(BaseDTO):
    """Response DTO for configuration section queries."""

    section: str = Field(description="Configuration section name")
    config: Dict[str, Any] = Field(description="Configuration section data")
    found: bool = Field(description="Whether the configuration section was found")
    validated_providers: List[str] = Field(
        default_factory=list, description="List of successfully validated providers"
    )
    failed_providers: List[str] = Field(
        default_factory=list, description="List of providers that failed validation"
    )
    validation_timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When validation was performed"
    )


class SystemStatusDTO(BaseDTO):
    """DTO for system status information."""

    status: str = Field(
        description="Overall system status (e.g., 'healthy', 'degraded', 'unhealthy')"
    )
    uptime_seconds: float = Field(description="System uptime in seconds")
    version: str = Field(description="Application version")
    environment: str = Field(description="Environment name (e.g., 'development', 'production')")
    active_connections: int = Field(description="Number of active connections")
    memory_usage_mb: float = Field(description="Memory usage in megabytes")
    cpu_usage_percent: float = Field(description="CPU usage percentage")
    disk_usage_percent: float = Field(description="Disk usage percentage")
    last_health_check: datetime = Field(
        default_factory=datetime.utcnow, description="Last health check timestamp"
    )
    components: Dict[str, str] = Field(
        default_factory=dict, description="Status of individual components"
    )


class ProviderMetricsDTO(BaseDTO):
    """DTO for provider performance metrics."""

    provider_name: str = Field(description="Name of the provider")
    total_requests: int = Field(description="Total number of requests processed")
    successful_requests: int = Field(description="Number of successful requests")
    failed_requests: int = Field(description="Number of failed requests")
    average_response_time_ms: float = Field(description="Average response time in milliseconds")
    error_rate_percent: float = Field(description="Error rate as percentage")
    throughput_per_minute: float = Field(description="Requests per minute")
    last_request_time: Optional[datetime] = Field(None, description="Timestamp of last request")
    uptime_percent: float = Field(description="Provider uptime percentage")
    health_status: str = Field(description="Current health status")
    metrics_timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When metrics were collected"
    )


class ProviderHealthDTO(BaseDTO):
    """DTO for provider health information."""

    provider_name: str = Field(description="Name of the provider")
    is_healthy: bool = Field(description="Whether the provider is healthy")
    health_score: float = Field(description="Health score (0.0 to 1.0)")
    last_health_check: datetime = Field(description="Last health check timestamp")
    response_time_ms: float = Field(description="Last response time in milliseconds")
    error_count: int = Field(description="Number of recent errors")
    status_message: str = Field(description="Human-readable status message")
    capabilities: List[str] = Field(default_factory=list, description="Provider capabilities")


class ProviderCapabilitiesDTO(BaseDTO):
    """DTO for provider capabilities information."""

    provider_name: str = Field(description="Name of the provider")
    supported_operations: List[str] = Field(description="List of supported operations")
    supported_instance_types: List[str] = Field(description="List of supported instance types")
    supported_regions: List[str] = Field(description="List of supported regions")
    max_instances: Optional[int] = Field(None, description="Maximum number of instances")
    supports_spot_instances: bool = Field(description="Whether spot instances are supported")
    supports_auto_scaling: bool = Field(description="Whether auto scaling is supported")
    api_version: str = Field(description="API version")
    last_updated: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )


class ProviderStrategyConfigDTO(BaseDTO):
    """DTO for provider strategy configuration."""

    strategy_name: str = Field(description="Name of the strategy")
    strategy_type: str = Field(description="Type of strategy (e.g., 'round_robin', 'weighted')")
    enabled_providers: List[str] = Field(description="List of enabled providers")
    provider_weights: Dict[str, float] = Field(
        default_factory=dict, description="Provider weights for weighted strategies"
    )
    failover_enabled: bool = Field(description="Whether failover is enabled")
    health_check_interval_seconds: int = Field(description="Health check interval in seconds")
    retry_attempts: int = Field(description="Number of retry attempts")
    timeout_seconds: int = Field(description="Timeout in seconds")
    last_modified: datetime = Field(
        default_factory=datetime.utcnow, description="Last modification timestamp"
    )


class ValidationDTO(BaseDTO):
    """DTO for template validation results."""

    is_valid: bool = Field(description="Whether the template is valid")
    validation_errors: List[str] = Field(
        default_factory=list, description="List of validation errors"
    )
    warnings: List[str] = Field(default_factory=list, description="List of validation warnings")
    template_id: str = Field(description="ID of the validated template")
    validation_timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When validation was performed"
    )
    schema_version: str = Field(description="Schema version used for validation")
