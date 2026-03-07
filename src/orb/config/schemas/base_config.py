"""Base configuration schemas shared across different config types."""

from pydantic import BaseModel, Field, field_validator


class BaseCircuitBreakerConfig(BaseModel):
    """Base circuit breaker configuration."""

    enabled: bool = Field(True, description="Enable circuit breaker")
    failure_threshold: int = Field(5, description="Number of failures before opening circuit")
    recovery_timeout: int = Field(60, description="Time to wait before attempting recovery")
    half_open_max_calls: int = Field(3, description="Max calls in half-open state")

    @field_validator("failure_threshold")
    @classmethod
    def validate_failure_threshold(cls, v: int) -> int:
        """Validate failure threshold."""
        if v <= 0:
            raise ValueError("Failure threshold must be positive")
        return v

    @field_validator("recovery_timeout")
    @classmethod
    def validate_recovery_timeout(cls, v: int) -> int:
        """Validate recovery timeout."""
        if v <= 0:
            raise ValueError("Recovery timeout must be positive")
        return v

    @field_validator("half_open_max_calls")
    @classmethod
    def validate_half_open_max_calls(cls, v: int) -> int:
        """Validate half open max calls."""
        if v <= 0:
            raise ValueError("Half open max calls must be positive")
        return v
