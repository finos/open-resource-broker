"""Template validation domain service."""

from dataclasses import dataclass, field
from typing import Any

from domain.base.exceptions import ConfigurationError, EntityNotFoundError
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.logging_port import LoggingPort
from domain.base.results import ValidationLevel, ValidationResult


@dataclass
class _ProviderCapabilities:
    """Domain-local capabilities value object used only within this service.

    Avoids importing infrastructure types (ProviderCapabilities) into the domain layer.
    """

    provider_type: str
    supported_apis: list[str] = field(default_factory=list)
    features: dict[str, Any] = field(default_factory=dict)

    def get_feature(self, feature_name: str, default: Any = None) -> Any:
        return self.features.get(feature_name, default)


class TemplateValidationDomainService:
    """Domain service for template validation business logic."""

    def __init__(self):
        self._config = None
        self._logger = None

    @property
    def config(self):
        # Return None if not available - service should handle gracefully
        return self._config

    @property
    def logger(self):
        # Return None if not available - service should handle gracefully
        return self._logger

    def inject_dependencies(self, config: ConfigurationPort, logger: LoggingPort):
        """Inject dependencies after container is ready."""
        self._config = config
        self._logger = logger
        self._initialized = False

    def _ensure_initialized(self):
        """Lazy initialization to avoid circular dependency during DI container setup."""
        if not self._initialized:
            self._initialized = True

    def validate_template_requirements(
        self,
        template: Any,
        provider_instance: str,
        validation_level: ValidationLevel = ValidationLevel.PERMISSIVE,
    ) -> ValidationResult:
        """Business logic for template validation."""
        self._ensure_initialized()

        result = ValidationResult(
            is_valid=True,
            errors=[],
            warnings=[],
            supported_features=[],
            unsupported_features=[],
            provider_instance=provider_instance,
        )

        try:
            capabilities = self._get_config_based_capabilities(provider_instance)

            self._validate_api_support(template, capabilities, result)
            self._validate_pricing_model(template, capabilities, result)
            self._validate_fleet_type_support(template, capabilities, result)
            self._validate_instance_limits(template, capabilities, result)

            if validation_level == ValidationLevel.STRICT and result.warnings:
                result.errors.extend(result.warnings)
                result.warnings = []
                result.is_valid = False
            elif validation_level == ValidationLevel.PERMISSIVE:
                result.warnings = []

            result.is_valid = len(result.errors) == 0

            if self.logger:
                self.logger.info(
                    "Validation result for %s: %s",
                    template.template_id,
                    "VALID" if result.is_valid else "INVALID",
                )

        except Exception as e:
            if self.logger:
                self.logger.error("Validation failed with exception: %s", str(e))
            result.is_valid = False
            result.errors.append(f"Validation error: {e!s}")

        return result

    def _get_config_based_capabilities(self, provider_instance: str) -> _ProviderCapabilities:
        """Get capabilities from merged provider configuration.

        Returns a domain-local capabilities object, avoiding any dependency on
        infrastructure types (ProviderCapabilities from providers.base.strategy).
        """
        self._ensure_initialized()

        if not self.config:
            raise ConfigurationError("No configuration manager available")

        provider_config = self.config.get_provider_instance_config(provider_instance)
        if not provider_config:
            raise EntityNotFoundError("ProviderInstance", provider_instance)

        provider_config_root = self.config.get_provider_config()
        provider_defaults = (
            provider_config_root.provider_defaults.get(provider_config.type)
            if provider_config_root is not None
            else None
        )  # type: ignore[union-attr]
        effective_handlers = provider_config.get_effective_handlers(provider_defaults)
        supported_apis = list(effective_handlers.keys())

        api_capabilities: dict[str, Any] = {}
        for api_name, handler_cfg in effective_handlers.items():
            extra = getattr(handler_cfg, "model_extra", None) or {}
            api_capabilities[api_name] = {
                "supports_spot": extra.get("supports_spot", False),
                "supports_on_demand": extra.get(
                    "supports_ondemand", extra.get("supports_on_demand", True)
                ),
                "supported_fleet_types": extra.get("supported_fleet_types") or [],
            }

        return _ProviderCapabilities(
            provider_type=provider_config.type,
            supported_apis=supported_apis,
            features={"api_capabilities": api_capabilities},
        )

    def _validate_api_support(self, template: Any, capabilities: Any, result: Any) -> None:
        """Validate that provider supports the required API."""
        if not template.provider_api:
            result.warnings.append("No provider API specified in template")
            return

        try:
            supported_apis = capabilities.supported_apis
            if template.provider_api not in supported_apis:
                result.errors.append(
                    f"Provider does not support API '{template.provider_api}'. Supported APIs: {supported_apis}"
                )
            else:
                result.supported_features.append(f"API: {template.provider_api}")
        except Exception as e:
            if self.logger:
                self.logger.error("Error in API validation: %s", e)
            result.errors.append(f"API validation error: {e}")

    def _validate_pricing_model(self, template: Any, capabilities: Any, result: Any) -> None:
        """Validate pricing model support (spot/on-demand)."""
        if not template.provider_api:
            return

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})
        price_type = getattr(template, "price_type", "ondemand")

        if price_type == "spot":
            if not api_caps.get("supports_spot", False):
                result.errors.append(
                    f"API '{template.provider_api}' does not support spot instances"
                )
            else:
                result.supported_features.append("Pricing: Spot instances")
        elif price_type == "ondemand":
            if not api_caps.get("supports_on_demand", True):
                result.errors.append(
                    f"API '{template.provider_api}' does not support on-demand instances"
                )
            else:
                result.supported_features.append("Pricing: On-demand instances")

    def _validate_fleet_type_support(self, template: Any, capabilities: Any, result: Any) -> None:
        """Validate fleet type support."""
        if not template.provider_api:
            return

        fleet_type = getattr(template, "fleet_type", None)
        if not fleet_type:
            fleet_type = template.metadata.get("fleet_type") if template.metadata else None

        if not fleet_type:
            return

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})
        supported_fleet_types = api_caps.get("supported_fleet_types", [])

        if supported_fleet_types and fleet_type not in supported_fleet_types:
            result.errors.append(
                f"API '{template.provider_api}' does not support fleet type '{fleet_type}'. Supported types: {supported_fleet_types}"
            )
        elif supported_fleet_types:
            result.supported_features.append(f"Fleet type: {fleet_type}")

    def _validate_instance_limits(self, template: Any, capabilities: Any, result: Any) -> None:
        """Validate instance count limits."""
        if not template.provider_api:
            return

        api_capabilities = capabilities.get_feature("api_capabilities", {})
        api_caps = api_capabilities.get(template.provider_api, {})
        max_instances = api_caps.get("max_instances", float("inf"))

        if template.max_instances > max_instances:
            result.errors.append(
                f"Requested {template.max_instances} instances exceeds API limit of {max_instances}"
            )
        else:
            result.supported_features.append(
                f"Instance count: {template.max_instances} (within limit)"
            )
