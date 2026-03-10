"""Retry configuration classes."""

from pydantic import BaseModel, Field


class RetryConfig(BaseModel):
    """Simplified retry configuration."""

    # Basic retry settings
    max_attempts: int = Field(3, description="Maximum retry attempts")
    base_delay: float = Field(1.0, description="Base delay in seconds")
    max_delay: float = Field(60.0, description="Maximum delay in seconds")
    jitter: bool = Field(True, description="Add jitter to delays")
