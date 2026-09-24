"""Unit tests for TemplateFactory."""

from unittest.mock import MagicMock

import pytest

from orb.domain.template.factory import TemplateFactory
from orb.domain.template.template_aggregate import Template

# ---------------------------------------------------------------------------
# Helper: minimal valid template data
# ---------------------------------------------------------------------------


def _tpl_data(**kwargs):
    defaults = {
        "template_id": "tpl-001",
        "max_instances": 5,
    }
    defaults.update(kwargs)
    return defaults


# ---------------------------------------------------------------------------
# TemplateFactory — basic creation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateFactoryCreate:
    def test_creates_core_template_without_provider(self):
        factory = TemplateFactory()
        tpl = factory.create_template(_tpl_data())
        assert isinstance(tpl, Template)
        assert tpl.template_id == "tpl-001"

    def test_create_with_unknown_provider_falls_back_to_core_template(self):
        factory = TemplateFactory()
        tpl = factory.create_template(_tpl_data(), provider_type="unknown_provider")
        assert isinstance(tpl, Template)

    def test_determines_provider_from_provider_type_field(self):
        factory = TemplateFactory()
        tpl = factory.create_template(_tpl_data(provider_type="aws"))
        # The explicit provider_type field is carried onto the built template.
        assert tpl.provider_type == "aws"

    def test_determines_provider_from_provider_name_with_dash(self):
        factory = TemplateFactory()
        # provider_name "aws-us-east-1" -> provider_type "aws" (derived by the
        # template's validate_provider_fields validator).
        tpl = factory.create_template(_tpl_data(provider_name="aws-us-east-1"))
        assert tpl.provider_name == "aws-us-east-1"
        assert tpl.provider_type == "aws"

    def test_no_provider_type_in_name_without_dash(self):
        # provider_name without dash => whole name treated as the provider type.
        factory = TemplateFactory()
        tpl = factory.create_template(_tpl_data(provider_name="aws"))
        assert tpl.provider_name == "aws"
        assert tpl.provider_type == "aws"


# ---------------------------------------------------------------------------
# TemplateFactory — register_provider_template_class
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateFactoryRegister:
    def test_supports_registered_provider(self):
        factory = TemplateFactory()
        assert not factory.supports_provider("testprovider")

        factory.register_provider_template_class("testprovider", Template)

        assert factory.supports_provider("testprovider")

    def test_non_template_subclass_raises(self):
        factory = TemplateFactory()
        with pytest.raises(ValueError, match="must inherit from Template"):
            factory.register_provider_template_class("bad", dict)  # type: ignore[arg-type]

    def test_get_supported_providers_empty_initially(self):
        factory = TemplateFactory()
        assert factory.get_supported_providers() == []

    def test_get_supported_providers_after_registration(self):
        factory = TemplateFactory()
        factory.register_provider_template_class("aws", Template)
        assert "aws" in factory.get_supported_providers()

    def test_registered_provider_uses_registered_class(self):
        class AwsTemplate(Template):
            pass

        factory = TemplateFactory()
        factory.register_provider_template_class("aws", AwsTemplate)
        tpl = factory.create_template(_tpl_data(), provider_type="aws")
        # The registered concrete class must be instantiated, not the core
        # Template fallback.
        assert type(tpl) is AwsTemplate

    def test_falls_back_to_core_template_when_registered_class_fails(self):
        """If the registered class raises on construction, fall back gracefully."""

        class BadTemplate(Template):
            def __init__(self, **data):
                raise RuntimeError("always fails")

        factory = TemplateFactory()
        factory.register_provider_template_class("aws", BadTemplate)
        # Should fall back to core Template without raising
        tpl = factory.create_template(_tpl_data(), provider_type="aws")
        assert isinstance(tpl, Template)


# ---------------------------------------------------------------------------
# TemplateFactory — with logger
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateFactoryWithLogger:
    def test_logs_registration_via_debug(self):
        logger = MagicMock()
        factory = TemplateFactory(logger=logger)
        factory.register_provider_template_class("aws", Template)
        # The registration log names the provider being registered.
        logger.debug.assert_called_once_with("Registered template class for provider: %s", "aws")

    def test_logs_create_when_provider_known(self):
        logger = MagicMock()
        factory = TemplateFactory(logger=logger)
        factory.register_provider_template_class("aws", Template)
        factory.create_template(_tpl_data(), provider_type="aws")
        # create_template logs which provider it is building for.
        logger.debug.assert_any_call("Creating template for provider: %s", "aws")


# ---------------------------------------------------------------------------
# TemplateFactory — create_template_with_extensions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTemplateFactoryWithExtensions:
    def test_merges_extension_data_into_template(self):
        factory = TemplateFactory()
        tpl = factory.create_template_with_extensions(
            _tpl_data(),
            provider_type=None,
            extension_data={"key_name": "my-key"},
        )
        assert tpl.key_name == "my-key"

    def test_extension_data_takes_priority_over_template_data(self):
        factory = TemplateFactory()
        tpl = factory.create_template_with_extensions(
            _tpl_data(max_instances=10),
            provider_type=None,
            extension_data={"max_instances": 1},
        )
        # extension_data wins: merge is {**template_data, **extension_data}
        assert tpl.max_instances == 1

    def test_without_extension_data_uses_template_data_copy(self):
        factory = TemplateFactory()
        tpl = factory.create_template_with_extensions(_tpl_data(), provider_type=None)
        assert isinstance(tpl, Template)

    def test_extension_registry_used_when_has_extension(self):
        registry = MagicMock()
        registry.has_extension.return_value = True
        registry.get_extension_defaults.return_value = {"monitoring_enabled": True}

        factory = TemplateFactory(extension_registry=registry)
        factory.register_provider_template_class("aws", Template)

        tpl = factory.create_template_with_extensions(
            _tpl_data(),
            provider_type="aws",
        )
        registry.get_extension_defaults.assert_called_once()
        # The fetched extension default must actually be merged into the template.
        assert tpl.monitoring_enabled is True

    def test_explicit_template_data_overrides_extension_default(self):
        registry = MagicMock()
        registry.has_extension.return_value = True
        registry.get_extension_defaults.return_value = {"monitoring_enabled": True}

        factory = TemplateFactory(extension_registry=registry)

        tpl = factory.create_template_with_extensions(
            _tpl_data(monitoring_enabled=False),
            provider_type="aws",
        )
        # Explicit template data wins over the lower-priority extension default.
        assert tpl.monitoring_enabled is False
