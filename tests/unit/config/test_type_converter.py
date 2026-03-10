"""Unit tests for ConfigTypeConverter — no AWS hardcoding."""

import inspect

import pytest

import orb.config.managers.type_converter as type_converter_module


@pytest.mark.unit
class TestTypeConverterNoAwsHardcoding:
    """Assert that type_converter.py contains no hardcoded AWS class/type strings."""

    def test_source_has_no_awsproviderconfig_string_match(self):
        """get_typed must not branch on class_name == 'AWSProviderConfig'."""
        source = inspect.getsource(type_converter_module)
        assert "AWSProviderConfig" not in source, (
            "type_converter.py must not reference 'AWSProviderConfig' by name"
        )

    def test_source_has_no_provider_type_equals_aws(self):
        """type_converter.py must not contain provider.get('type') == 'aws'."""
        source = inspect.getsource(type_converter_module)
        assert '== "aws"' not in source
        assert "== 'aws'" not in source

    def test_no_get_aws_provider_config_method(self):
        """The old _get_aws_provider_config method must not exist."""
        assert not hasattr(type_converter_module.ConfigTypeConverter, "_get_aws_provider_config")


@pytest.mark.unit
class TestGetTypedProviderConfig:
    """get_typed resolves provider config via ProviderSettingsRegistry."""

    def _raw_config_with_aws(self) -> dict:
        return {
            "provider": {
                "providers": [
                    {
                        "name": "aws-primary",
                        "type": "aws",
                        "enabled": True,
                        "config": {"region": "us-east-1"},
                    }
                ],
                "active_provider": "aws-primary",
            }
        }

    def test_get_typed_aws_provider_config_via_registry(self):
        """get_typed(AWSProviderConfig) resolves through ProviderSettingsRegistry."""
        # Ensure AWS is registered
        from orb.providers.aws.registration import register_aws_provider_settings

        register_aws_provider_settings()

        from orb.providers.aws.configuration.config import AWSProviderConfig

        converter = type_converter_module.ConfigTypeConverter(self._raw_config_with_aws())
        result = converter.get_typed(AWSProviderConfig)

        assert isinstance(result, AWSProviderConfig)

    def test_get_typed_uses_generic_fallback_for_unregistered_class(self):
        """get_typed falls back to section-name lookup for classes not in registry."""

        # A config class not registered in ProviderSettingsRegistry
        class PerformanceConfig:
            def __init__(self, max_workers: int = 4, **kwargs):
                self.max_workers = max_workers

        raw = {"performance": {"max_workers": 8}}
        converter = type_converter_module.ConfigTypeConverter(raw)
        result = converter.get_typed(PerformanceConfig)
        assert result.max_workers == 8

    def test_get_typed_provider_config_for_type_uses_provider_type_param(self):
        """_get_provider_config_for_type uses the provider_type param, not literal 'aws'."""
        converter = type_converter_module.ConfigTypeConverter(self._raw_config_with_aws())
        # Call the renamed method directly with provider_type='aws'
        from orb.providers.aws.configuration.config import AWSProviderConfig

        result = converter._get_provider_config_for_type("aws", AWSProviderConfig)
        assert isinstance(result, AWSProviderConfig)

    def test_get_typed_provider_config_wrong_type_raises(self):
        """_get_provider_config_for_type raises when no matching provider found."""
        raw = {
            "provider": {
                "providers": [
                    {"name": "aws-primary", "type": "aws", "enabled": True, "config": {}}
                ],
                "active_provider": "aws-primary",
            }
        }
        converter = type_converter_module.ConfigTypeConverter(raw)
        from orb.providers.aws.configuration.config import AWSProviderConfig

        with pytest.raises(Exception, match="gcp"):
            converter._get_provider_config_for_type("gcp", AWSProviderConfig)
