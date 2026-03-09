"""Main configuration validator orchestrator."""

from typing import Any, Optional

from orb.config.schemas import AppConfig, validate_config
from orb.providers.registry import get_provider_registry


class ValidationResult:
    """Configuration validation result."""

    def __init__(
        self, errors: Optional[list[str]] = None, warnings: Optional[list[str]] = None
    ) -> None:
        """Initialize the instance."""
        self.errors = errors or []
        self.warnings = warnings or []
        self.is_valid = len(self.errors) == 0

    def add_error(self, error: str) -> None:
        """Add validation error."""
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str) -> None:
        """Add validation warning."""
        self.warnings.append(warning)


class ConfigValidator:
    """Main configuration validator orchestrator."""

    def __init__(self) -> None:
        """Initialize the configuration validator."""

    def validate_config(self, config_data: dict[str, Any]) -> ValidationResult:
        """
        Validate complete configuration.

        Args:
            config_data: Configuration data to validate

        Returns:
            ValidationResult with errors and warnings
        """
        result = ValidationResult()

        try:
            # Use Pydantic validation for schema validation
            app_config = validate_config(config_data)

            # Additional business logic validation can be added here
            self._validate_business_rules(app_config, result)

        except Exception as e:
            result.add_error(f"Configuration validation failed: {e!s}")

        return result

    def _validate_business_rules(self, config: AppConfig, result: ValidationResult) -> None:
        """
        Validate business rules beyond schema validation.

        Args:
            config: Validated configuration object
            result: Validation result to update
        """
        # Validate performance settings
        if config.performance.max_workers > 50:
            result.add_warning("High number of max_workers may cause resource contention")

        # Validate storage configuration
        if config.storage.strategy == "sql":
            sql_config = config.storage.sql_strategy
            if sql_config.pool_size > 20:
                result.add_warning("Large SQL connection pool size may consume excessive resources")

    def validate_provider_config(
        self, provider_type: str, provider_config: dict[str, Any]
    ) -> ValidationResult:
        """
        Validate provider-specific configuration.

        Args:
            provider_type: Type of provider (e.g., 'aws')
            provider_config: Provider configuration data

        Returns:
            ValidationResult with provider-specific validation
        """
        result = ValidationResult()

        if not get_provider_registry().is_provider_registered(provider_type):
            result.add_error(f"Unsupported provider type: {provider_type}")

        return result
