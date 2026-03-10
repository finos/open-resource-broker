"""Unit tests for ConfigValidator — provider-registry-driven validation."""

import inspect

import pytest

import orb.config.validators.config_validator as config_validator_module

ConfigValidator = config_validator_module.ConfigValidator
ValidationResult = config_validator_module.ValidationResult


@pytest.mark.unit
class TestConfigValidatorNoAwsHardcoding:
    """Assert that config_validator contains no hardcoded 'aws' provider checks."""

    def test_source_has_no_provider_type_equals_aws(self):
        """config_validator.py must not contain provider_type == 'aws'."""
        source = inspect.getsource(config_validator_module)
        assert 'provider_type == "aws"' not in source
        assert "provider_type == 'aws'" not in source

    def test_source_has_no_provider_type_equals_aws_in_business_rules(self):
        """_validate_business_rules must not gate on provider.type == 'aws'."""
        source = inspect.getsource(config_validator_module)
        assert 'provider.type == "aws"' not in source
        assert "provider.type == 'aws'" not in source


@pytest.mark.unit
class TestValidateProviderConfigRegistryDriven:
    """validate_provider_config must use the provider registry, not hardcoded strings."""

    def test_registered_provider_type_is_valid(self):
        """A provider type registered in the registry should pass validation."""
        validator = ConfigValidator()
        result = validator.validate_provider_config("testprovider", {}, lambda _: True)
        assert result.is_valid, f"Expected valid, got errors: {result.errors}"

    def test_unregistered_provider_type_is_invalid(self):
        """A provider type not in the registry should produce a validation error."""
        validator = ConfigValidator()
        result = validator.validate_provider_config("nonexistent_provider_xyz", {}, lambda _: False)
        assert not result.is_valid
        assert any("nonexistent_provider_xyz" in e for e in result.errors)

    def test_aws_provider_valid_when_registered(self):
        """'aws' passes validation when the caller confirms it is registered."""
        validator = ConfigValidator()
        result = validator.validate_provider_config("aws", {}, lambda _: True)
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
        aws_warnings = [
            w for w in result.warnings if "aws_max_retries" in w or "aws_read_timeout" in w
        ]
        assert aws_warnings == [], f"Unexpected AWS warnings: {aws_warnings}"
