"""System events - Configuration, lifecycle, security, and performance monitoring."""

from typing import Any, Optional

from pydantic import Field

from .base_events import ErrorEvent, InfrastructureEvent, PerformanceEvent, TimedEvent

# =============================================================================
# SYSTEM-WIDE EVENTS
# =============================================================================


class SystemEvent(InfrastructureEvent):
    """Base class for system-wide events."""

    component: str
    system_context: dict[str, Any] = Field(default_factory=dict)


# Configuration Events
class ConfigurationLoadedEvent(SystemEvent, TimedEvent):
    """Event raised when configuration is loaded."""

    config_source: str  # "file", "environment", "default"
    config_version: str
    loaded_sections: list[str] = Field(default_factory=list)
    load_duration_ms: float = Field(alias="duration_ms")  # Use inherited duration_ms


class ConfigurationChangedEvent(SystemEvent):
    """Event raised when configuration changes."""

    changed_keys: list[str] = Field(default_factory=list)
    old_values: dict[str, Any] = Field(default_factory=dict)
    new_values: dict[str, Any] = Field(default_factory=dict)
    change_source: str  # "file_reload", "environment_update", "runtime_change"


class ConfigurationErrorEvent(SystemEvent, ErrorEvent):
    """Event raised when configuration loading fails."""

    config_source: str
    fallback_used: bool = False


# Application Lifecycle Events
class ApplicationStartedEvent(SystemEvent, TimedEvent):
    """Event raised when the application starts."""

    startup_duration_ms: float = Field(alias="duration_ms")  # Use inherited duration_ms
    mode: str  # "script", "rest", "eda"
    configuration_loaded: bool
    version: Optional[str] = None
    environment: Optional[str] = None


class ApplicationShutdownEvent(SystemEvent, TimedEvent):
    """Event raised when the application shuts down."""

    shutdown_reason: str  # "normal", "error", "signal", "timeout"
    uptime_seconds: float = Field(alias="duration_ms")  # Reuse duration_ms for uptime
    requests_processed: int
    graceful_shutdown: bool = True


class ApplicationErrorEvent(SystemEvent, ErrorEvent):
    """Event raised for critical application errors."""

    error_type: str
    stack_trace: Optional[str] = None
    recovery_attempted: bool = False


# Security and Audit Events
class SecurityEvent(InfrastructureEvent):
    """Event raised for security-related operations."""

    event_type: str  # "authentication", "authorization", "access_denied", "suspicious_activity"
    user_context: str
    resource_accessed: str
    success: bool
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class AuditTrailEvent(InfrastructureEvent):
    """Event raised for audit trail purposes."""

    action: str  # "create", "read", "update", "delete"
    entity_type: str
    entity_id: str
    user_context: str
    before_state: Optional[dict[str, Any]] = None
    after_state: Optional[dict[str, Any]] = None


class ComplianceEvent(InfrastructureEvent):
    """Event raised for compliance monitoring."""

    compliance_type: str  # "data_retention", "access_control", "encryption", "audit_log"
    compliance_status: str  # "compliant", "non_compliant", "warning"
    policy_name: str
    violation_details: Optional[str] = None


# Performance and Monitoring Events
class PerformanceMetricEvent(SystemEvent, PerformanceEvent):
    """Event raised for performance metrics."""

    metric_name: str
    metric_value: float
    metric_unit: str  # "ms", "bytes", "count", "percent"
    threshold_value: Optional[float] = None


class HealthCheckEvent(SystemEvent, PerformanceEvent):
    """Event raised for system health checks."""

    check_name: str
    health_status: str  # "healthy", "degraded", "unhealthy"
    response_time_ms: float = Field(alias="duration_ms")  # Use inherited duration_ms
    check_details: dict[str, Any] = Field(default_factory=dict)
    dependencies_status: dict[str, str] = Field(default_factory=dict)
