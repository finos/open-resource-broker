"""Unit tests for ConfigValidator — provider-registry-driven validation."""

import pytest

from orb.config.validators.config_validator import ConfigValidator, ValidationResult


@pytest.mark.unit
class TestConfigValidatorNoAwsHardcoding:
    """Assert that config_validator contains no hardcoded 'aws' provider checks."""

    def test_source_has_no_provider_type_equals_aws(self):
        """config_validator.py must not contain provider_type == 'aws'."""
        import inspect
        import orb.config.validators.config_validator as mod

        source = inspect.getsource(mod)
        assert "provider_type == \"aws\"" not in source
        assert "provider_type == 'aws'" not in source

    def test_source_has_no_provider_type_equals_aws_in_business_rules(self):
        """_validate_business_rules must not gate on provider.type == 'aws'."""
        import inspect
        import orb.config.validators.config_validator as mod

        source = inspect.getsource(mod)
        assert "provider.type == \"aws\"" not in source
        assert "provider.type == 'aws'" not in source


@pytest.mark.unit
class TestValidateProviderConfigRegistryDriven:
    """validate_provider_config must use the provider registry, not hardcoded strings."""

    def test_registered_provider_type_is_valid(self):
        """A provider type registered in the registry should pass validation."""
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        # Register a fake provider type for this test
        registry.register_provider(
            provider_type="testprovider",
            strategy_factory=lambda cfg: None,
            config_factory=lambda data: None,
        )
        try:
            validator = ConfigValidator()
            result = validator.validate_provider_config("testprovider", {})
            assert result.is_valid, f"Expected valid, got errors: {result.errors}"
        finally:
            registry.unregister_provider("testprovider")

    def test_unregistered_provider_type_is_invalid(self):
        """A provider type not in the registry should produce a validation error."""
        validator = ConfigValidator()
        result = validator.validate_provider_config("nonexistent_provider_xyz", {})
        assert not result.is_valid
        assert any("nonexistent_provider_xyz" in e for e in result.errors)

    def test_aws_provider_valid_when_registered(self):
        """'aws' passes validation because it is registered by aws/registration.py."""
        # Importing aws registration auto-registers 'aws' in ProviderSettingsRegistry.
        # The ProviderRegistry registration happens lazily; ensure it is present.
        from orb.providers.aws.registration import register_aws_provider
        from orb.providers.registry import get_provider_registry

        registry = get_provider_registry()
        if not registry.is_provider_registered("aws"):
            register_aws_provider(registry)

        validator = ConfigValidator()
        result = validator.validate_provider_config("aws", {})
        assert result.is_valid, f"Expected valid for 'aws', got errors: {result.errors}"


@pytest.mark.unit
class TestValidateBusinessRulesNoAwsBlock:
    """_validate_business_rules must not emit AWS-specific warnings for non-AWS providers."""

    def _make_minimal_config_data(self) -> dict:
        return {
            "provider": {
                "providers": [],
                "active_provider": None,
            },
            "performance": {"max_workers": 4},
            "storage": {"strategy": "memory"},
            "app": {"name": "test", "environment": "test"},
            "logging": {},
            "scheduler": {},
            "resource": {},
        }

    def test_no_aws_warnings_for_non_aws_provider(self):
        """No AWS-specific warnings should appear when provider type is not aws."""
        validator = ConfigValidator()
        config_data = self._make_minimal_config_data()
        # Provide a non-aws provider with fields that would trigger AWS warnings
        config_data["provider"]["providers"] = [
            {
                "name": "myprovider",
                "type": "other",
                "enabled": True,
                "config": {"aws_max_retries": 99, "aws_read_timeout": 999},
            }
        ]
        result = validator.validate_config(config_data)
        aws_warnings = [w for w in result.warnings if "aws_max_retries" in w or "aws_read_timeout" in w]
        assert aws_warnings == [], f"Unexpected AWS warnings: {aws_warnings}"
