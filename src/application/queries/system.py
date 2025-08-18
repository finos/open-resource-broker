"""System-level queries for administrative operations."""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from application.interfaces.command_query import Query


class GetSystemStatusQuery(Query, BaseModel):
    """Query to get system health status."""

    model_config = ConfigDict(frozen=True)

    include_provider_health: bool = True
    detailed: bool = False


class GetProviderConfigQuery(Query, BaseModel):
    """Query to get current provider configuration information."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    include_sensitive: bool = False


class GetProviderMetricsQuery(Query, BaseModel):
    """Query to get provider performance metrics."""

    model_config = ConfigDict(frozen=True)

    provider_name: Optional[str] = None
    time_range: Optional[str] = "1h"
    detailed: bool = False


class GetConfigurationQuery(Query, BaseModel):
    """Query to get configuration values."""

    model_config = ConfigDict(frozen=True)

    key: str
    default: Optional[Any] = None
    section: Optional[str] = None


class GetConfigurationSectionQuery(Query, BaseModel):
    """Query to get entire configuration section."""

    model_config = ConfigDict(frozen=True)

    section: str
    include_defaults: bool = True


class ValidateProviderConfigQuery(Query, BaseModel):
    """Query to validate current provider configuration."""

    model_config = ConfigDict(frozen=True)

    detailed: bool = True
