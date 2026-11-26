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

    def test_get_typed_aws_provider_config_via_registry(self, monkeypatch):
        """get_typed(AWSProviderConfig) resolves through ProviderSettingsRegistry."""
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
        from orb.providers.aws.configuration.config import AWSProviderConfig

        monkeypatch.setitem(ProviderSettingsRegistry._settings_classes, "aws", AWSProviderConfig)

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


@pytest.mark.unit
class TestGetTypedUnregisteredProviderWarning:
    """get_typed warns when a provider settings class has no registration."""

    _WARNING = "No provider settings registered for"

    @pytest.fixture
    def module_logger(self, monkeypatch):
        from unittest.mock import MagicMock

        mock_logger = MagicMock()
        monkeypatch.setattr(type_converter_module, "logger", mock_logger)
        return mock_logger

    def _warned(self, mock_logger) -> bool:
        return any(
            call.args and str(call.args[0]).startswith(self._WARNING)
            for call in mock_logger.warning.call_args_list
        )

    def test_warns_and_returns_defaults_when_provider_not_registered(
        self, monkeypatch, module_logger
    ):
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
        from orb.providers.aws.configuration.config import AWSProviderConfig

        monkeypatch.setattr(ProviderSettingsRegistry, "_settings_classes", {})
        converter = type_converter_module.ConfigTypeConverter({})

        result = converter.get_typed(AWSProviderConfig)

        assert isinstance(result, AWSProviderConfig)
        assert self._warned(module_logger)
        assert module_logger.warning.call_args.args[1] == "AWSProviderConfig"

    def test_no_warning_when_provider_registered(self, monkeypatch, module_logger):
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
        from orb.providers.aws.configuration.config import AWSProviderConfig

        monkeypatch.setattr(
            ProviderSettingsRegistry, "_settings_classes", {"aws": AWSProviderConfig}
        )
        raw = {"provider": {"providers": [{"name": "a", "type": "aws", "config": {}}]}}
        converter = type_converter_module.ConfigTypeConverter(raw)

        converter.get_typed(AWSProviderConfig)

        assert not self._warned(module_logger)

    def test_no_warning_for_non_provider_class(self, monkeypatch, module_logger):
        from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry

        monkeypatch.setattr(ProviderSettingsRegistry, "_settings_classes", {})

        class PerformanceConfig:
            def __init__(self, max_workers: int = 4, **kwargs):
                self.max_workers = max_workers

        converter = type_converter_module.ConfigTypeConverter({"performance": {"max_workers": 2}})

        assert converter.get_typed(PerformanceConfig).max_workers == 2
        assert not self._warned(module_logger)
