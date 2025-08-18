"""Request metadata and configuration value objects."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from pydantic import field_validator, model_validator

from domain.base.value_objects import ValueObject


class RequestTimeout(ValueObject):
    """
    Value object representing a request timeout.

    Attributes:
        seconds: Timeout duration in seconds
    """

    seconds: int

    @field_validator("seconds")
    @classmethod
    def validate_seconds(cls, v: int) -> int:
        """Validate timeout seconds value.

        Args:
            v: Timeout value in seconds

        Returns:
            Validated timeout value

        Raises:
            ValueError: If timeout is invalid (not integer, not positive, or exceeds 1 day)
        """
        if not isinstance(v, int):
            raise ValueError("Timeout must be an integer") from e

        if v <= 0:
            raise ValueError("Timeout must be positive") from e

        # Reasonable upper limit (1 day)
        if v > 86400:
            raise ValueError("Timeout cannot exceed 86400 seconds (1 day)")

        return v

    @property
    def duration(self) -> timedelta:
        """Get timeout as timedelta."""
        return timedelta(seconds=self.seconds)

    @property
    def expiry_time(self) -> datetime:
        """Get expiry time from now."""
        return datetime.utcnow() + self.duration

    def is_expired(self, start_time: datetime) -> bool:
        """Check if timeout has expired."""
        return (datetime.utcnow() - start_time) > self.duration

    @classmethod
    def from_seconds(cls, seconds: int) -> "RequestTimeout":
        """Create timeout from seconds."""
        return cls(seconds=seconds)

    @classmethod
    def default(cls) -> "RequestTimeout":
        """Create default timeout from configuration."""
        try:
            from domain.base.configuration_service import get_domain_config_service

            config_service = get_domain_config_service()
            if config_service:
                timeout = config_service.get_default_timeout()
            else:
                # Fallback if service not available
                timeout = 300
        except ImportError:
            # Fallback if service not available
            timeout = 300

        return cls(seconds=timeout)


class MachineCount(ValueObject):
    """
    Value object representing a machine count.

    Attributes:
        value: Number of machines
        max_allowed: Maximum allowed machines
    """

    value: int
    max_allowed: Optional[int] = None

    @model_validator(mode="after")
    def validate_machine_count(self) -> "MachineCount":
        """Validate machine count constraints.

        Returns:
            Self after validation

        Raises:
            ValueError: If machine count is invalid or exceeds maximum allowed
        """
        if not isinstance(self.value, int):
            raise ValueError("Machine count must be an integer") from e

        if self.value <= 0:
            raise ValueError("Machine count must be positive") from e

        # Get max allowed from configuration if not provided
        max_allowed = self.max_allowed
        if max_allowed is None:
            try:
                from domain.base.configuration_service import get_domain_config_service

                config_service = get_domain_config_service()
                if config_service:
                    max_allowed = config_service.get_max_machines_per_request()
                else:
                    max_allowed = 100  # Fallback default

                object.__setattr__(self, "max_allowed", max_allowed)
            except Exception:
                # Fallback if config not available
                max_allowed = 100  # Default limit
                object.__setattr__(self, "max_allowed", max_allowed)

        if self.value > max_allowed:
            raise ValueError(f"Machine count cannot exceed {max_allowed}") from e

        return self

    def __str__(self) -> str:
        """Return string representation of machine count.

        Returns:
            Machine count as string
        """
        return str(self.value)

    def __int__(self) -> int:
        """Integer representation of machine count.

        Returns:
            Machine count as integer
        """
        return self.value

    @classmethod
    def from_int(cls, value: int, max_allowed: Optional[int] = None) -> "MachineCount":
        """Create count from integer."""
        return cls(value=value, max_allowed=max_allowed)


class RequestTag(ValueObject):
    """
    Value object representing a request tag.

    Attributes:
        key: Tag key
        value: Tag value
    """

    key: str
    value: str

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        """Validate tag key format.

        Args:
            v: Tag key to validate

        Returns:
            Validated tag key

        Raises:
            ValueError: If key is empty or invalid format
        """
        if not v or not isinstance(v, str):
            raise ValueError("Tag key must be a non-empty string") from e

        # AWS tag key restrictions
        if len(v) > 128:
            raise ValueError("Tag key cannot exceed 128 characters") from e

        return v.strip()

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: str) -> str:
        """Validate tag value format.

        Args:
            v: Tag value to validate

        Returns:
            Validated tag value

        Raises:
            ValueError: If value exceeds maximum length
        """
        if not isinstance(v, str):
            raise ValueError("Tag value must be a string") from e

        # AWS tag value restrictions
        if len(v) > 256:
            raise ValueError("Tag value cannot exceed 256 characters") from e

        return v.strip()

    def __str__(self) -> str:
        """Return string representation of tag in key=value format.

        Returns:
            Tag formatted as 'key=value'
        """
        return f"{self.key}={self.value}"

    @classmethod
    def from_string(cls, tag_string: str) -> "RequestTag":
        """Create tag from key=value string."""
        if "=" not in tag_string:
            raise ValueError("Tag string must be in format 'key=value'") from e

        key, value = tag_string.split("=", 1)
        return cls(key=key.strip(), value=value.strip())


class RequestConfiguration(ValueObject):
    """
    Configuration settings for a request.

    This contains all the configuration parameters that control how
    a request is processed, including provider-specific settings.

    Attributes:
        template_id: ID of the template to use
        machine_count: Number of machines to provision
        timeout: Request timeout settings
        tags: Tags to apply to resources
        provider_config: Provider-specific configuration
        retry_config: Retry configuration
        notification_config: Notification settings
    """

    template_id: str
    machine_count: int
    timeout: int = 3600  # Default 1 hour
    tags: Dict[str, str] = {}
    provider_config: Dict[str, Any] = {}
    retry_config: Dict[str, Any] = {}
    notification_config: Dict[str, Any] = {}

    @field_validator("template_id")
    @classmethod
    def validate_template_id(cls, v: str) -> str:
        """Validate template ID format.

        Args:
            v: Template ID to validate

        Returns:
            Validated template ID

        Raises:
            ValueError: If template ID is empty or invalid
        """
        if not v or not isinstance(v, str):
            raise ValueError("Template ID must be a non-empty string") from e
        return v.strip()

    @field_validator("machine_count")
    @classmethod
    def validate_machine_count(cls, v: int) -> int:
        """Validate machine count constraints.

        Args:
            v: Machine count to validate

        Returns:
            Validated machine count

        Raises:
            ValueError: If machine count is invalid or exceeds limits
        """
        if not isinstance(v, int) or v <= 0:
            raise ValueError("Machine count must be a positive integer") from e

        # Basic upper limit check
        if v > 1000:
            raise ValueError("Machine count cannot exceed 1000") from e

        return v

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        """Validate timeout value.

        Args:
            v: Timeout in seconds

        Returns:
            Validated timeout value

        Raises:
            ValueError: If timeout is invalid or exceeds maximum
        """
        if not isinstance(v, int) or v <= 0:
            raise ValueError("Timeout must be a positive integer") from e

        # Upper limit check (1 day)
        if v > 86400:
            raise ValueError("Timeout cannot exceed 86400 seconds (1 day)")

        return v

    def get_timeout_object(self) -> RequestTimeout:
        """Get timeout as RequestTimeout object."""
        return RequestTimeout(seconds=self.timeout)

    def get_machine_count_object(self) -> MachineCount:
        """Get machine count as MachineCount object."""
        return MachineCount(value=self.machine_count)

    def get_tags_list(self) -> List[RequestTag]:
        """Get tags as list of RequestTag objects."""
        return [RequestTag(key=k, value=v) for k, v in self.tags.items()]

    def add_tag(self, key: str, value: str) -> "RequestConfiguration":
        """Add a tag and return new configuration."""
        new_tags = self.tags.copy()
        new_tags[key] = value

        return RequestConfiguration(
            template_id=self.template_id,
            machine_count=self.machine_count,
            timeout=self.timeout,
            tags=new_tags,
            provider_config=self.provider_config.copy(),
            retry_config=self.retry_config.copy(),
            notification_config=self.notification_config.copy(),
        )

    def with_provider_config(self, config: Dict[str, Any]) -> "RequestConfiguration":
        """Set provider config and return new configuration."""
        return RequestConfiguration(
            template_id=self.template_id,
            machine_count=self.machine_count,
            timeout=self.timeout,
            tags=self.tags.copy(),
            provider_config=config.copy(),
            retry_config=self.retry_config.copy(),
            notification_config=self.notification_config.copy(),
        )


class LaunchTemplateInfo(ValueObject):
    """
    Information about a launch template used in a request.

    This contains metadata about the launch template that was used
    to provision machines, including version information and configuration.

    Attributes:
        template_id: Launch template ID
        template_name: Launch template name (optional)
        version: Template version used
        configuration: Template configuration snapshot
    """

    template_id: str
    template_name: Optional[str] = None
    version: str = "$Latest"
    configuration: Dict[str, Any] = {}

    @field_validator("template_id")
    @classmethod
    def validate_template_id(cls, v: str) -> str:
        """Validate template ID format."""
        if not v or not isinstance(v, str):
            raise ValueError("Template ID must be a non-empty string") from e
        return v.strip()

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        """Validate template version format."""
        if not v or not isinstance(v, str):
            raise ValueError("Template version must be a non-empty string") from e
        return v.strip()

    def __str__(self) -> str:
        name_part = f" ({self.template_name})" if self.template_name else ""
        return f"{self.template_id}{name_part} v{self.version}"

    def is_latest_version(self) -> bool:
        """Check if this uses the latest version."""
        return self.version.lower() in ["$latest", "latest"]

    def get_display_name(self) -> str:
        """Get a display-friendly name."""
        return self.template_name or self.template_id


class RequestHistoryEvent(ValueObject):
    """
    Event that occurred during request processing.

    This represents significant events in the request lifecycle,
    such as status changes, errors, or completion milestones.

    Attributes:
        event_type: Type of event (e.g., 'status_change', 'error', 'completion')
        timestamp: When the event occurred
        message: Human-readable event message
        details: Additional event details
        source: Source of the event (e.g., 'system', 'user', 'provider')
    """

    event_type: str
    timestamp: str  # ISO format datetime string
    message: str
    details: Dict[str, Any] = {}
    source: str = "system"

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        """Validate event type format."""
        if not v or not isinstance(v, str):
            raise ValueError("Event type must be a non-empty string") from e

        # Normalize to lowercase with underscores
        normalized = v.lower().replace("-", "_").replace(" ", "_")
        return normalized

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, v: str) -> str:
        """Validate timestamp format."""
        if not v or not isinstance(v, str):
            raise ValueError("Timestamp must be a non-empty string") from e

        # Basic ISO format validation
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Timestamp must be in ISO format") from e

        return v

    @field_validator("message")
    @classmethod
    def validate_message(cls, v: str) -> str:
        """Validate event message format."""
        if not v or not isinstance(v, str):
            raise ValueError("Event message must be a non-empty string") from e
        return v.strip()

    def __str__(self) -> str:
        return f"[{self.timestamp}] {self.event_type}: {self.message}"

    @classmethod
    def create(
        cls,
        event_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        source: str = "system",
    ) -> "RequestHistoryEvent":
        """Create a new event with current timestamp."""
        return cls(
            event_type=event_type,
            timestamp=datetime.utcnow().isoformat(),
            message=message,
            details=details or {},
            source=source,
        )

    def is_error_event(self) -> bool:
        """Check if this is an error event."""
        return self.event_type in ["error", "failure", "exception"]

    def is_status_change_event(self) -> bool:
        """Check if this is a status change event."""
        return self.event_type in ["status_change", "state_change"]
