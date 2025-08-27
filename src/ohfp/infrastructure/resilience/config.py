"""Retry configuration classes."""

from typing import Any

from pydantic import BaseModel, Field


class RetryConfig(BaseModel):
    """Simplified retry configuration."""

    # Basic retry settings
    max_attempts: int = Field(3, description="Maximum retry attempts")
    base_delay: float = Field(1.0, description="Base delay in seconds")
    max_delay: float = Field(60.0, description="Maximum delay in seconds")
    jitter: bool = Field(True, description="Add jitter to delays")

    # Generic retry configuration - provider-specific configs should be in
    # provider layer

    def get_service_config(self, service: str) -> dict[str, Any]:
        """
        Get retry configuration for a specific service.

        Args:
            service: AWS service name

        Returns:
            Service-specific configuration or default values
        """
        service_config = self.service_configs.get(service, {})

        return {
            "max_attempts": service_config.get("max_attempts", self.max_attempts),
            "base_delay": service_config.get("base_delay", self.base_delay),
            "max_delay": service_config.get("max_delay", self.max_delay),
            "jitter": service_config.get("jitter", self.jitter),
        }
