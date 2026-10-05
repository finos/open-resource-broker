"""Unit tests for the AWS provider registration module.

Covers config/strategy/validator factories, the default-API loader, the
provider/instance/extension/auth registration functions, and the
entry-point plugin hook — using mocks for the registry/container/logger
collaborators rather than exercising real AWS infrastructure.

Several of the functions under test mutate process-global registries
(TemplateExtensionRegistry, the provider_plugin _initialized_providers
guard, and this module's own _REGISTERED_PROVIDERS sentinel). The real
AuthRegistry singleton is never touched directly — every
register_aws_auth_strategies test patches get_auth_registry() with a
MagicMock. Fixtures below snapshot and restore the remaining global state
so tests stay order-independent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.providers.aws import registration as reg
from orb.providers.base.provider_plugin import reset_for_testing


@pytest.fixture(autouse=True)
def _isolate_global_registries():
    """Snapshot/restore global registry state mutated by this module."""
    from orb.infrastructure.registry.template_extension_registry import (
        TemplateExtensionRegistry,
    )

    extensions_snapshot = dict(TemplateExtensionRegistry._extensions)
    registered_providers_snapshot = list(reg._REGISTERED_PROVIDERS)
    reset_for_testing()

    yield

    TemplateExtensionRegistry._extensions.clear()
    TemplateExtensionRegistry._extensions.update(extensions_snapshot)
    reg._REGISTERED_PROVIDERS.clear()
    reg._REGISTERED_PROVIDERS.extend(registered_providers_snapshot)
    reset_for_testing()


# ---------------------------------------------------------------------------
# create_aws_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateAwsConfig:
    def test_creates_config_from_dict(self) -> None:
        config = reg.create_aws_config({"region": "us-east-1"})
        assert config.region == "us-east-1"

    def test_wraps_generic_exception_as_runtime_error(self) -> None:
        with patch(
            "orb.providers.aws.configuration.config.AWSProviderConfig",
            side_effect=ValueError("bad config"),
        ):
            with pytest.raises(RuntimeError, match="Failed to create AWS config"):
                reg.create_aws_config({"region": "us-east-1"})


# ---------------------------------------------------------------------------
# register_aws_provider_settings
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAwsProviderSettings:
    def test_registers_when_not_already_registered(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_or_none.return_value = None
        with patch(
            "orb.config.schemas.provider_settings_registry.ProviderSettingsRegistry",
            mock_registry,
        ):
            reg.register_aws_provider_settings()
        mock_registry.register_provider_settings.assert_called_once()

    def test_skips_when_already_registered(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_or_none.return_value = object()
        with patch(
            "orb.config.schemas.provider_settings_registry.ProviderSettingsRegistry",
            mock_registry,
        ):
            reg.register_aws_provider_settings()
        mock_registry.register_provider_settings.assert_not_called()

    def test_generic_exception_raises_runtime_error(self) -> None:
        mock_registry = MagicMock()
        mock_registry.get_or_none.side_effect = RuntimeError("boom")
        with patch(
            "orb.config.schemas.provider_settings_registry.ProviderSettingsRegistry",
            mock_registry,
        ):
            with pytest.raises(RuntimeError, match="Failed to register AWS provider settings"):
                reg.register_aws_provider_settings()


# ---------------------------------------------------------------------------
# create_aws_resolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateAwsResolver:
    def test_always_returns_none(self) -> None:
        assert reg.create_aws_resolver() is None


# ---------------------------------------------------------------------------
# create_aws_validator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateAwsValidator:
    def test_returns_none_when_provider_config_is_none(self) -> None:
        assert reg.create_aws_validator(None) is None

    def test_returns_none_for_unsupported_type(self) -> None:
        assert reg.create_aws_validator(12345) is None

    def test_builds_validator_from_aws_provider_config_instance(self) -> None:
        from orb.providers.aws.configuration.config import AWSProviderConfig

        aws_config = AWSProviderConfig(region="us-east-1")  # type: ignore[call-arg]
        validator = reg.create_aws_validator(aws_config)
        assert validator is not None
        assert validator._config is aws_config

    def test_builds_validator_from_object_with_config_attribute(self) -> None:
        instance_config = MagicMock()
        instance_config.config = {"region": "us-west-2"}
        validator = reg.create_aws_validator(instance_config)
        assert validator is not None
        assert validator._config.region == "us-west-2"

    def test_builds_validator_from_raw_dict(self) -> None:
        validator = reg.create_aws_validator({"region": "eu-west-1"})
        assert validator is not None
        assert validator._config.region == "eu-west-1"

    def test_wraps_exception_as_runtime_error(self) -> None:
        with patch(
            "orb.providers.aws.configuration.config.AWSProviderConfig",
            side_effect=ValueError("bad config"),
        ):
            with pytest.raises(RuntimeError, match="Failed to create AWS validator"):
                reg.create_aws_validator({"region": "us-east-1"})


# ---------------------------------------------------------------------------
# _load_aws_default_api
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLoadAwsDefaultApi:
    def test_reads_default_provider_api_from_real_defaults_file(self) -> None:
        # Exercises the real aws_defaults.json shipped with the package.
        assert reg._load_aws_default_api() == "EC2Fleet"

    def test_returns_none_when_file_missing(self) -> None:
        with patch("orb.providers.aws.registration.Path") as mock_path_cls:
            mock_path_cls.return_value.parent.__truediv__.return_value.read_text.side_effect = (
                FileNotFoundError("no file")
            )
            assert reg._load_aws_default_api() is None

    def test_returns_none_when_json_malformed(self) -> None:
        with patch("orb.providers.aws.registration.json.loads", side_effect=ValueError("bad json")):
            assert reg._load_aws_default_api() is None


# ---------------------------------------------------------------------------
# register_aws_provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAwsProvider:
    def test_registers_as_provider_type_by_default(self) -> None:
        mock_registry = MagicMock()
        mock_logger = MagicMock()
        reg.register_aws_provider(registry=mock_registry, logger=mock_logger)
        mock_registry.register_provider.assert_called_once()
        kwargs = mock_registry.register_provider.call_args.kwargs
        assert kwargs["provider_type"] == "aws"
        assert kwargs["default_api"] == "EC2Fleet"
        mock_logger.info.assert_called_once()

    def test_registers_as_named_instance_when_instance_name_given(self) -> None:
        mock_registry = MagicMock()
        reg.register_aws_provider(registry=mock_registry, instance_name="aws-secondary")
        mock_registry.register_provider_instance.assert_called_once()
        kwargs = mock_registry.register_provider_instance.call_args.kwargs
        assert kwargs["instance_name"] == "aws-secondary"
        mock_registry.register_provider.assert_not_called()

    def test_resolves_default_registry_when_none_given(self) -> None:
        mock_registry = MagicMock()
        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            reg.register_aws_provider()
        mock_registry.register_provider.assert_called_once()

    def test_exception_is_logged_and_reraised(self) -> None:
        mock_registry = MagicMock()
        mock_registry.register_provider.side_effect = RuntimeError("registry exploded")
        mock_logger = MagicMock()
        with pytest.raises(RuntimeError, match="registry exploded"):
            reg.register_aws_provider(registry=mock_registry, logger=mock_logger)
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# register_aws_provider_instance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAwsProviderInstance:
    def test_registers_type_and_instance_when_type_not_registered(self) -> None:
        provider_instance = MagicMock()
        provider_instance.name = "aws-primary"
        provider_instance.config = {"region": "us-east-1"}

        mock_registry = MagicMock()
        mock_registry.is_provider_registered.return_value = False

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            result = reg.register_aws_provider_instance(provider_instance)

        assert result is True
        mock_registry.register_provider.assert_called_once()
        mock_registry.register_provider_instance.assert_called_once()

    def test_skips_type_registration_when_already_registered(self) -> None:
        provider_instance = MagicMock()
        provider_instance.name = "aws-primary"
        provider_instance.config = {"region": "us-east-1"}

        mock_registry = MagicMock()
        mock_registry.is_provider_registered.return_value = True

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            result = reg.register_aws_provider_instance(provider_instance)

        assert result is True
        mock_registry.register_provider.assert_not_called()
        mock_registry.register_provider_instance.assert_called_once()

    def test_returns_false_and_logs_config_snippet_on_failure(self) -> None:
        provider_instance = MagicMock()
        provider_instance.name = "aws-broken"
        provider_instance.config = {"region": "us-east-1", "profile": "default"}

        mock_registry = MagicMock()
        mock_registry.is_provider_registered.return_value = True
        mock_registry.register_provider_instance.side_effect = RuntimeError("nope")
        mock_logger = MagicMock()

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            result = reg.register_aws_provider_instance(provider_instance, logger=mock_logger)

        assert result is False
        mock_logger.error.assert_called_once()
        error_args = mock_logger.error.call_args.args
        assert "us-east-1" in error_args
        assert "default" in error_args

    def test_returns_false_when_config_missing_entirely(self) -> None:
        """getattr(provider_instance, 'config', None) or {} guards a missing config."""
        provider_instance = MagicMock(spec=["name"])
        provider_instance.name = "aws-no-config"

        mock_registry = MagicMock()
        mock_registry.is_provider_registered.return_value = True
        mock_registry.register_provider_instance.side_effect = RuntimeError("nope")

        with patch("orb.providers.registry.get_provider_registry", return_value=mock_registry):
            result = reg.register_aws_provider_instance(provider_instance)

        assert result is False


# ---------------------------------------------------------------------------
# register_aws_extensions
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAwsExtensions:
    def test_registers_extension_when_absent(self) -> None:
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
        )

        TemplateExtensionRegistry._extensions.pop("aws", None)
        reg.register_aws_extensions()
        assert TemplateExtensionRegistry.has_extension("aws")

    def test_idempotent_skip_when_already_registered(self) -> None:
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
        )

        reg.register_aws_extensions()
        assert TemplateExtensionRegistry.has_extension("aws")
        # Second call must be a safe no-op (covered by not raising / not re-registering).
        reg.register_aws_extensions()
        assert TemplateExtensionRegistry.has_extension("aws")

    def test_exception_is_logged_and_reraised(self) -> None:
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
        )

        TemplateExtensionRegistry._extensions.pop("aws", None)
        mock_logger = MagicMock()
        with patch.object(
            TemplateExtensionRegistry,
            "register_extension",
            side_effect=RuntimeError("registration boom"),
        ):
            with pytest.raises(RuntimeError, match="registration boom"):
                reg.register_aws_extensions(logger=mock_logger)
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# register_aws_template_factory
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAwsTemplateFactory:
    def test_registers_aws_template_class_with_factory(self) -> None:
        mock_factory = MagicMock()
        mock_logger = MagicMock()
        reg.register_aws_template_factory(mock_factory, logger=mock_logger)
        mock_factory.register_provider_template_class.assert_called_once()
        args = mock_factory.register_provider_template_class.call_args.args
        assert args[0] == "aws"
        mock_logger.info.assert_called_once()

    def test_does_not_raise_when_factory_registration_fails(self) -> None:
        mock_factory = MagicMock()
        mock_factory.register_provider_template_class.side_effect = RuntimeError("boom")
        mock_logger = MagicMock()
        # Must not raise — factory registration is explicitly optional.
        reg.register_aws_template_factory(mock_factory, logger=mock_logger)
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# get_aws_extension_defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAwsExtensionDefaults:
    def test_returns_template_defaults_dict(self) -> None:
        defaults = reg.get_aws_extension_defaults()
        assert isinstance(defaults, dict)


# ---------------------------------------------------------------------------
# register_aws_auth_strategies
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAwsAuthStrategies:
    def test_registers_iam_and_cognito_when_absent(self) -> None:
        mock_registry = MagicMock()
        mock_registry.is_registered.return_value = False
        with patch(
            "orb.infrastructure.auth.registry.get_auth_registry", return_value=mock_registry
        ):
            reg.register_aws_auth_strategies()
        assert mock_registry.register_strategy.call_count == 2
        registered_names = {c.args[0] for c in mock_registry.register_strategy.call_args_list}
        assert registered_names == {"iam", "cognito"}

    def test_skips_already_registered_strategies(self) -> None:
        mock_registry = MagicMock()
        mock_registry.is_registered.return_value = True
        with patch(
            "orb.infrastructure.auth.registry.get_auth_registry", return_value=mock_registry
        ):
            reg.register_aws_auth_strategies()
        mock_registry.register_strategy.assert_not_called()

    def test_import_error_is_caught_and_warned(self) -> None:
        mock_logger = MagicMock()
        with patch(
            "orb.infrastructure.auth.registry.get_auth_registry",
            side_effect=ImportError("auth module missing"),
        ):
            reg.register_aws_auth_strategies(logger=mock_logger)
        mock_logger.warning.assert_called_once()

    def test_generic_exception_is_logged_and_reraised(self) -> None:
        mock_logger = MagicMock()
        with patch(
            "orb.infrastructure.auth.registry.get_auth_registry",
            side_effect=RuntimeError("registry boom"),
        ):
            with pytest.raises(RuntimeError, match="registry boom"):
                reg.register_aws_auth_strategies(logger=mock_logger)
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# is_aws_provider_registered
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsAwsProviderRegistered:
    def test_true_after_registering_extension(self) -> None:
        reg.register_aws_extensions()
        assert reg.is_aws_provider_registered() is True

    def test_false_when_extension_absent(self) -> None:
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
        )

        TemplateExtensionRegistry._extensions.pop("aws", None)
        assert reg.is_aws_provider_registered() is False


# ---------------------------------------------------------------------------
# register_aws_plugin — entry-point hook idempotency
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAwsPlugin:
    def test_calls_register_aws_provider_once(self) -> None:
        reg._REGISTERED_PROVIDERS.clear()
        with patch("orb.providers.aws.registration.register_aws_provider") as mock_register:
            reg.register_aws_plugin()
        mock_register.assert_called_once()
        assert "aws" in reg._REGISTERED_PROVIDERS

    def test_second_call_is_a_no_op(self) -> None:
        reg._REGISTERED_PROVIDERS.clear()
        reg._REGISTERED_PROVIDERS.append("aws")
        with patch("orb.providers.aws.registration.register_aws_provider") as mock_register:
            reg.register_aws_plugin()
        mock_register.assert_not_called()


# ---------------------------------------------------------------------------
# register_aws_services_with_di
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterAwsServicesWithDi:
    def test_registers_services_when_not_already_registered(self) -> None:
        mock_logger = MagicMock()
        mock_container = MagicMock()
        mock_container.is_registered.return_value = False

        def _get(port):
            from orb.domain.base.ports import LoggingPort

            if port is LoggingPort:
                return mock_logger
            return MagicMock()

        mock_container.get.side_effect = _get

        reg.register_aws_services_with_di(mock_container)

        assert mock_container.register_singleton.called
        mock_logger.debug.assert_called()

    def test_skips_cache_and_resolver_when_already_registered(self) -> None:
        mock_logger = MagicMock()
        mock_container = MagicMock()
        mock_container.is_registered.return_value = True

        def _get(port):
            from orb.domain.base.ports import LoggingPort

            if port is LoggingPort:
                return mock_logger
            return MagicMock()

        mock_container.get.side_effect = _get

        reg.register_aws_services_with_di(mock_container)
        mock_logger.debug.assert_called()

    def test_exception_is_caught_and_warned_not_raised(self) -> None:
        mock_logger = MagicMock()
        mock_container = MagicMock()

        def _get(port):
            from orb.domain.base.ports import LoggingPort

            if port is LoggingPort:
                return mock_logger
            return MagicMock()

        mock_container.get.side_effect = _get
        # is_registered() is the first call inside the try block; raising
        # here exercises the outer except-and-warn path.
        mock_container.is_registered.side_effect = RuntimeError("container exploded")

        # Must not raise — failures here are logged as warnings only.
        reg.register_aws_services_with_di(mock_container)
        mock_logger.warning.assert_called_once()


# ---------------------------------------------------------------------------
# initialize_aws_provider — verifies the declarative spec built from this
# module's optional-import guards, without re-running the (separately
# tested) register_provider_complete orchestration logic.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInitializeAwsProvider:
    def test_builds_spec_with_aws_provider_name_and_settings(self) -> None:
        from orb.providers.aws.configuration.config import AWSProviderConfig
        from orb.providers.aws.domain.template.aws_template_dto_config import (
            AWSTemplateDTOConfig,
        )

        captured: dict = {}

        def _capture_spec(spec, logger=None):
            captured["spec"] = spec

        with patch(
            "orb.providers.base.registration.register_provider_complete",
            side_effect=_capture_spec,
        ):
            reg.initialize_aws_provider()

        spec = captured["spec"]
        assert spec.provider_name == "aws"
        assert spec.settings_class is AWSProviderConfig
        assert spec.dto_config_class is AWSTemplateDTOConfig
        assert spec.register_auth is not None
        assert spec.extra_init is not None

    def test_register_auth_closure_delegates_to_register_aws_auth_strategies(self) -> None:
        captured: dict = {}

        def _capture_spec(spec, logger=None):
            captured["spec"] = spec

        with patch(
            "orb.providers.base.registration.register_provider_complete",
            side_effect=_capture_spec,
        ):
            reg.initialize_aws_provider()

        with patch("orb.providers.aws.registration.register_aws_auth_strategies") as mock_auth:
            captured["spec"].register_auth(None)
        mock_auth.assert_called_once_with(None)
