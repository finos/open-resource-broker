"""Core provider interfaces - contracts that all providers must implement."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class BaseProviderConfig(BaseModel):
    """Base configuration for providers."""

    model_config = ConfigDict(extra="allow")  # Allow provider-specific config fields

    provider_type: str
    region: Optional[str] = None


# Alias for backward compatibility
ProviderConfig = BaseProviderConfig
