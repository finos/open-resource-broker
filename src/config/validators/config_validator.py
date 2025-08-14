"""Main configuration validator orchestrator."""

from typing import Any, Dict, List

from src.config.schemas import AppConfig, validate_config


class ValidationResult:
    """Configuration validation result."""

    def __init__(self, errors: List[str] = None, warnings: List[str] = None):
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

    def __init__(self):
        """Initialize the configuration validator."""

    def validate_config(self, config_data: Dict[str, Any]) -> ValidationResult:
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
            result.add_error(f"Configuration validation failed: {str(e)}")

        return result

    def _validate_business_rules(self, config: AppConfig, result: ValidationResult) -> None:
        """
        Validate business rules beyond schema validation.

        Args:
            config: Validated configuration object
            result: Validation result to update
        """
        # Validate provider instances
        if hasattr(config.provider, "providers") and config.provider.providers:
            for provider in config.provider.providers:
                if provider.type == "aws" and hasattr(provider, "config"):
                    aws_config = provider.config

                    # Validate AWS-specific business rules
                    if hasattr(aws_config, "max_retries") and aws_config.max_retries > 10:
                        result.add_warning(
                            f"AWS provider '{provider.name}' max_retries is very high, consider reducing for better performance"
                        )

                    if hasattr(aws_config, "timeout") and aws_config.timeout > 300:
                        result.add_warning(
                            f"AWS provider '{provider.name}' timeout is very high, consider reducing to avoid long waits"
                        )

        # Validate template configuration
        if config.template:
            template_config = config.template

            if len(template_config.subnet_ids) > 16:
                result.add_error("Too many subnet IDs specified (maximum 16)")

            if len(template_config.security_group_ids) > 5:
                result.add_error("Too many security group IDs specified (maximum 5)")

        # Validate performance settings
        if config.performance.max_workers > 50:
            result.add_warning("High number of max_workers may cause resource contention")

        # Validate storage configuration
        if config.storage.strategy == "sql":
            sql_config = config.storage.sql_strategy
            if sql_config.pool_size > 20:
                result.add_warning("Large SQL connection pool size may consume excessive resources")

    def validate_provider_config(
        self, provider_type: str, provider_config: Dict[str, Any]
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

        if provider_type == "aws":
            # AWS-specific validation logic would go here
            # For now, rely on Pydantic schema validation
            pass
        else:
            result.add_error(f"Unsupported provider type: {provider_type}")

        return result
