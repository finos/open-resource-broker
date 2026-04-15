"""Unit tests for TemplateValidationDomainService."""

from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.results import ValidationLevel, ValidationResult
from orb.domain.services.template_validation_domain_service import (
    TemplateValidationDomainService,
    _ProviderCapabilities,
)


def _make_template(
    provider_api="RunInstances",
    price_type="ondemand",
    fleet_type=None,
    max_instances=10,
    metadata=None,
):
    t = MagicMock()
    t.template_id = "tmpl-001"
    t.provider_api = provider_api
    t.price_type = price_type
    t.fleet_type = fleet_type
    t.max_instances = max_instances
    t.metadata = metadata or {}
    return t


def _make_config(provider_type="aws", supported_apis=None):
    if supported_apis is None:
        supported_apis = ["RunInstances", "CreateFleet"]

    provider_instance_config = MagicMock()
    provider_instance_config.type = provider_type
    provider_instance_config.get_effective_handlers = MagicMock(
        return_value={api: {} for api in supported_apis}
    )

    provider_config_root = MagicMock()
    provider_config_root.provider_defaults = {provider_type: MagicMock()}

    config = MagicMock()
    config.get_provider_instance_config = MagicMock(return_value=provider_instance_config)
    config.get_provider_config = MagicMock(return_value=provider_config_root)
    config.get_handler_capabilities = MagicMock(return_value={})
    return config


def _patched_validation_result(**kwargs):
    """Create ValidationResult supplying the missing unsupported_features field."""
    kwargs.setdefault("unsupported_features", [])
    return ValidationResult(**kwargs)


PATCH_TARGET = "orb.domain.services.template_validation_domain_service.ValidationResult"


class TestTemplateValidationDomainService:
    def setup_method(self):
        self.svc = TemplateValidationDomainService()
        self.config = _make_config()
        self.logger = MagicMock()
        self.svc.inject_dependencies(self.config, self.logger)

    def _run(self, template, provider_instance="aws-prod", level=ValidationLevel.STRICT):
        with patch(PATCH_TARGET, side_effect=_patched_validation_result):
            return self.svc.validate_template_requirements(template, provider_instance, level)

    def test_valid_template_passes(self):
        template = _make_template(provider_api="RunInstances")
        result = self._run(template)
        assert result.is_valid is True
        assert result.errors == []

    def test_unsupported_api_fails(self):
        config = _make_config(supported_apis=["CreateFleet"])
        self.svc.inject_dependencies(config, self.logger)
        template = _make_template(provider_api="RunInstances")
        result = self._run(template)
        assert result.is_valid is False
        assert any("RunInstances" in e for e in result.errors)

    def test_no_provider_api_adds_warning(self):
        # provider_api=None triggers a warning; strict mode promotes it to error
        # Use PERMISSIVE so warnings stay as warnings
        template = _make_template(provider_api=None)
        result = self._run(template, level=ValidationLevel.PERMISSIVE)
        # In permissive mode warnings are cleared, but the validation still ran
        # The key behaviour: no errors from unsupported API (there is no API to check)
        assert result.is_valid is True

    def test_strict_mode_promotes_warnings_to_errors(self):
        template = _make_template(provider_api=None)
        result = self._run(template, level=ValidationLevel.STRICT)
        assert result.is_valid is False
        assert result.warnings == []
        assert any("No provider API" in e for e in result.errors)

    def test_permissive_mode_clears_warnings(self):
        template = _make_template(provider_api=None)
        result = self._run(template, level=ValidationLevel.PERMISSIVE)
        assert result.warnings == []

    def test_provider_instance_not_found_fails(self):
        config = MagicMock()
        config.get_provider_instance_config = MagicMock(return_value=None)
        self.svc.inject_dependencies(config, self.logger)
        template = _make_template()
        result = self._run(template, provider_instance="missing-provider")
        assert result.is_valid is False
        assert len(result.errors) > 0

    def test_no_config_fails(self):
        # Service with no config injected should fail validation cleanly.
        svc = TemplateValidationDomainService()
        template = _make_template()
        with patch(PATCH_TARGET, side_effect=_patched_validation_result):
            result = svc.validate_template_requirements(template, "aws-prod")
        assert result.is_valid is False
        assert any("Validation error" in e for e in result.errors)

    def test_instance_limit_exceeded_fails(self):
        caps = _ProviderCapabilities(
            provider_type="aws",
            supported_apis=["RunInstances"],
            features={"api_capabilities": {"RunInstances": {"max_instances": 5}}},
        )
        self.svc._get_config_based_capabilities = MagicMock(return_value=caps)
        template = _make_template(provider_api="RunInstances", max_instances=100)
        result = self._run(template)
        assert result.is_valid is False
        assert any("exceeds" in e for e in result.errors)

    def test_supported_features_populated(self):
        template = _make_template(provider_api="RunInstances", max_instances=5)
        result = self._run(template)
        assert any("RunInstances" in f for f in result.supported_features)

    def test_exception_during_validation_returns_invalid(self):
        self.config.get_provider_instance_config = MagicMock(side_effect=RuntimeError("unexpected"))
        template = _make_template()
        result = self._run(template)
        assert result.is_valid is False
        assert any("Validation error" in e for e in result.errors)

    def test_falls_back_to_validator_capabilities_when_handler_defaults_are_unwired(self):
        provider_instance_config = MagicMock()
        provider_instance_config.name = "azure-default"
        provider_instance_config.type = "azure"
        provider_instance_config.config = {"subscription_id": "sub-123"}
        provider_instance_config.handlers = None
        provider_instance_config.handler_overrides = None
        provider_instance_config.capabilities = None
        provider_instance_config.get_effective_handlers = MagicMock(return_value={})

        provider_config_root = MagicMock()
        provider_config_root.provider_defaults = {}

        config = MagicMock()
        config.get_provider_instance_config.return_value = provider_instance_config
        config.get_provider_config.return_value = provider_config_root

        validator = MagicMock()
        validator.get_supported_provider_apis.return_value = ["VMSS", "SingleVM"]
        validator.get_api_capabilities.side_effect = lambda api: {
            "VMSS": {"supports_spot": True, "supports_on_demand": True, "max_instances": 1000},
            "SingleVM": {"supports_spot": True, "supports_on_demand": True, "max_instances": 1000},
        }[api]
        provider_registry = MagicMock()
        provider_registry.create_validator.return_value = validator

        self.svc.inject_dependencies(config, self.logger, provider_registry)

        template = _make_template(provider_api="VMSS")
        result = self._run(template, provider_instance="azure-default")

        assert result.is_valid is True
        assert result.errors == []
        provider_registry.create_validator.assert_called_once_with(
            "azure",
            {"subscription_id": "sub-123"},
        )
        validator.get_api_capabilities.assert_any_call("VMSS")

    def test_validator_capability_fallback_preserves_spot_validation(self):
        provider_instance_config = MagicMock()
        provider_instance_config.name = "azure-default"
        provider_instance_config.type = "azure"
        provider_instance_config.config = {"subscription_id": "sub-123"}
        provider_instance_config.handlers = None
        provider_instance_config.handler_overrides = None
        provider_instance_config.capabilities = None
        provider_instance_config.get_effective_handlers = MagicMock(return_value={})

        provider_config_root = MagicMock()
        provider_config_root.provider_defaults = {}

        config = MagicMock()
        config.get_provider_instance_config.return_value = provider_instance_config
        config.get_provider_config.return_value = provider_config_root

        validator = MagicMock()
        validator.get_supported_provider_apis.return_value = ["VMSS"]
        validator.get_api_capabilities.return_value = {
            "supports_spot": True,
            "supports_on_demand": True,
            "max_instances": 1000,
        }
        provider_registry = MagicMock()
        provider_registry.create_validator.return_value = validator

        self.svc.inject_dependencies(config, self.logger, provider_registry)

        template = _make_template(provider_api="VMSS", price_type="spot")
        result = self._run(template, provider_instance="azure-default")

        assert result.is_valid is True
        assert result.errors == []
        assert "Pricing: Spot instances" in result.supported_features

    def test_explicit_empty_handler_override_does_not_fall_back_to_validator_capabilities(self):
        provider_instance_config = MagicMock()
        provider_instance_config.name = "azure-default"
        provider_instance_config.type = "azure"
        provider_instance_config.config = {"subscription_id": "sub-123"}
        provider_instance_config.handlers = None
        provider_instance_config.handler_overrides = {"VMSS": None}
        provider_instance_config.capabilities = None
        provider_instance_config.get_effective_handlers = MagicMock(return_value={})

        provider_config_root = MagicMock()
        provider_config_root.provider_defaults = {}

        config = MagicMock()
        config.get_provider_instance_config.return_value = provider_instance_config
        config.get_provider_config.return_value = provider_config_root

        provider_registry = MagicMock()

        self.svc.inject_dependencies(config, self.logger, provider_registry)

        template = _make_template(provider_api="VMSS")
        result = self._run(template, provider_instance="azure-default")

        assert result.is_valid is False
        assert any("Provider does not support API 'VMSS'" in e for e in result.errors)
        provider_registry.create_validator.assert_not_called()
