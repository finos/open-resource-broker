"""Provider strategy configuration schemas."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .base_config import BaseCircuitBreakerConfig


class HandlerConfig(BaseModel):
    """Handler configuration with flexible additional fields."""

    model_config = ConfigDict(extra="allow")

    handler_class: str = Field(..., description="Handler class name")

    def merge_with(self, override: "HandlerConfig") -> "HandlerConfig":
        """Merge this handler config with an override, returning a new config."""
        # Start with base config
        merged_data = self.model_dump()

        # Apply overrides
        override_data = override.model_dump()
        for key, value in override_data.items():
            merged_data[key] = value

        return HandlerConfig(**merged_data)


class ProviderDefaults(BaseModel):
    """Default configuration for a provider type."""

    handlers: Dict[str, HandlerConfig] = Field(
        default_factory=dict, description="Default handler configurations"
    )
    template_defaults: Dict[str, Any] = Field(
        default_factory=dict, description="Template defaults for this provider type"
    )
    extensions: Optional[Dict[str, Any]] = Field(
        None, description="Provider-specific extensions configuration"
    )


class ProviderMode(str, Enum):
    """Provider operation modes."""

    SINGLE = "single"
    MULTI = "multi"
    NONE = "none"


class HealthCheckConfig(BaseModel):
    """Health check configuration for individual provider."""

    enabled: bool = Field(True, description="Enable health checks for this provider")
    interval: int = Field(300, description="Health check interval in seconds")
    timeout: int = Field(30, description="Health check timeout in seconds")
    retry_count: int = Field(3, description="Number of retries for failed health checks")

    @field_validator("interval")
    @classmethod
    def validate_interval(cls, v: int) -> int:
        """Validate health check interval."""
        if v <= 0:
            raise ValueError("Health check interval must be positive") from e
        return v

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        """Validate health check timeout."""
        if v <= 0:
            raise ValueError("Health check timeout must be positive") from e
        return v


class CircuitBreakerConfig(BaseCircuitBreakerConfig):
    """Provider-specific circuit breaker configuration."""

    @field_validator("recovery_timeout")
    @classmethod
    def validate_recovery_timeout(cls, v: int) -> int:
        """Validate recovery timeout."""
        if v <= 0:
            raise ValueError("Recovery timeout must be positive") from e
        return v


class ProviderInstanceConfig(BaseModel):
    """Configuration for individual provider instance."""

    name: str = Field(..., description="Unique name for this provider instance")
    type: str = Field(..., description="Provider type (aws, provider1, provider2)")
    enabled: bool = Field(True, description="Whether this provider is enabled")
    priority: int = Field(0, description="Provider priority (lower = higher priority)")
    weight: int = Field(100, description="Provider weight for load balancing")
    config: Dict[str, Any] = Field(
        default_factory=dict, description="Provider-specific configuration"
    )

    # Handler configuration with inheritance support
    handlers: Optional[Dict[str, HandlerConfig]] = Field(
        None, description="Full handler override (ignores defaults)"
    )
    handler_overrides: Optional[Dict[str, Optional[HandlerConfig]]] = Field(
        None, description="Partial handler overrides (null to disable)"
    )

    # Template defaults for this provider instance
    template_defaults: Optional[Dict[str, Any]] = Field(
        None, description="Template defaults for this provider instance"
    )

    # Provider instance extensions
    extensions: Optional[Dict[str, Any]] = Field(
        None, description="Instance-specific extension overrides"
    )

    health_check: HealthCheckConfig = Field(
        default_factory=HealthCheckConfig, description="Health check configuration"
    )

    def get_effective_handlers(
        self, provider_defaults: Optional[ProviderDefaults] = None
    ) -> Dict[str, HandlerConfig]:
        """Get effective handlers after applying defaults and overrides."""

        # If full handlers override is specified, use it directly
        if self.handlers:
            return self.handlers

        # Start with provider type defaults
        effective_handlers = {}
        if provider_defaults and provider_defaults.handlers:
            effective_handlers = {
                name: config for name, config in provider_defaults.handlers.items()
            }

        # Apply handler overrides
        if self.handler_overrides:
            for handler_name, override_config in self.handler_overrides.items():
                if override_config is None:
                    # Remove handler (null override)
                    effective_handlers.pop(handler_name, None)
                else:
                    # Merge or add handler
                    if handler_name in effective_handlers:
                        # Merge with existing default
                        effective_handlers[handler_name] = effective_handlers[
                            handler_name
                        ].merge_with(override_config)
                    else:
                        # New handler not in defaults
                        effective_handlers[handler_name] = override_config

        return effective_handlers

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate provider name."""
        if not v or not v.strip():
            raise ValueError("Provider name cannot be empty") from e
        # Ensure name is valid for use as identifier
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                "Provider name must contain only alphanumeric characters, hyphens, and underscores"
            )
        return v.strip()

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        """Validate provider type."""
        valid_types = ["aws", "provider1", "provider2"]  # Extensible list
        if v not in valid_types:
            raise ValueError(f"Provider type must be one of {valid_types}") from e
        return v

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, v: int) -> int:
        """Validate provider weight."""
        if v <= 0:
            raise ValueError("Provider weight must be positive") from e
        return v


class ProviderConfig(BaseModel):
    """Provider configuration supporting single and multi-provider modes with comprehensive features."""

    # Provider strategy configuration
    selection_policy: str = Field(
        "FIRST_AVAILABLE", description="Default provider selection policy"
    )
    active_provider: Optional[str] = Field(
        None, description="Active provider for single-provider mode"
    )
    default_provider_type: Optional[str] = Field(
        None, description="Default provider type for templates"
    )
    default_provider_instance: Optional[str] = Field(
        None, description="Default provider instance for templates"
    )
    health_check_interval: int = Field(300, description="Global health check interval in seconds")
    circuit_breaker: CircuitBreakerConfig = Field(
        default_factory=CircuitBreakerConfig,
        description="Circuit breaker configuration",
    )

    # Provider defaults and instances
    provider_defaults: Dict[str, ProviderDefaults] = Field(
        default_factory=dict, description="Default configurations by provider type"
    )
    providers: List[ProviderInstanceConfig] = Field(
        default_factory=list, description="List of provider instances"
    )

    @field_validator("selection_policy")
    @classmethod
    def validate_selection_policy(cls, v: str) -> str:
        """Validate selection policy."""
        valid_policies = [
            "FIRST_AVAILABLE",
            "ROUND_ROBIN",
            "WEIGHTED_ROUND_ROBIN",
            "LEAST_CONNECTIONS",
            "FASTEST_RESPONSE",
            "HIGHEST_SUCCESS_RATE",
            "CAPABILITY_BASED",
            "HEALTH_BASED",
            "RANDOM",
            "PERFORMANCE_BASED",
        ]
        if v not in valid_policies:
            raise ValueError(f"Selection policy must be one of {valid_policies}") from e
        return v

    @field_validator("health_check_interval")
    @classmethod
    def validate_health_check_interval(cls, v: int) -> int:
        """Validate health check interval."""
        if v <= 0:
            raise ValueError("Health check interval must be positive") from e
        return v

    @model_validator(mode="after")
    def validate_provider_configuration(self) -> "ProviderConfig":
        """Validate overall provider configuration."""
        # Validate active_provider exists if specified
        if self.active_provider:
            provider_names = [p.name for p in self.providers]
            if self.active_provider not in provider_names:
                raise ValueError(
                    f"Active provider '{self.active_provider}' not found in providers list"
                )

        # Validate unique provider names
        provider_names = [p.name for p in self.providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("Provider names must be unique") from e

        # Validate at least one provider is configured
        if not self.providers:
            raise ValueError("At least one provider must be configured") from e

        return self

    def get_mode(self) -> ProviderMode:
        """Determine provider operation mode - strategy mode only."""
        if self.active_provider:
            return ProviderMode.SINGLE
        elif not self.providers:
            return ProviderMode.NONE
        else:
            # Count enabled providers
            enabled_providers = [p for p in self.providers if p.enabled]
            if len(enabled_providers) > 1:
                return ProviderMode.MULTI
            elif len(enabled_providers) == 1:
                return ProviderMode.SINGLE
            elif len(self.providers) == 1:
                return ProviderMode.SINGLE  # Single provider, even if disabled
            else:
                return ProviderMode.NONE

    def get_active_providers(self) -> List[ProviderInstanceConfig]:
        """Get active providers based on selection policy."""

        # Multi-provider policies should return ALL enabled providers
        multi_provider_policies = [
            "WEIGHTED_ROUND_ROBIN",
            "ROUND_ROBIN",
            "LEAST_CONNECTIONS",
            "PERFORMANCE_BASED",
            "FASTEST_RESPONSE",
            "HIGHEST_SUCCESS_RATE",
            "CAPABILITY_BASED",
            "HEALTH_BASED",
        ]

        if self.selection_policy in multi_provider_policies:
            # Multi-provider mode - return all enabled providers
            return [p for p in self.providers if p.enabled]

        # Single provider mode
        if self.active_provider:
            # Explicit single provider specified
            return [p for p in self.providers if p.name == self.active_provider]

        # Default: return all enabled providers for backward compatibility
        return [p for p in self.providers if p.enabled]

    def is_multi_provider_mode(self) -> bool:
        """Check if configuration is in multi-provider mode."""
        return self.get_mode() == ProviderMode.MULTI

    def get_provider_by_name(self, name: str) -> Optional[ProviderInstanceConfig]:
        """Get provider configuration by name."""
        for provider in self.providers:
            if provider.name == name:
                return provider
        return None
