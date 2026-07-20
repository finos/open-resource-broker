"""Unit tests for k8s registration.py — uncovered logic.

Covers:
  registration.py :: 64, 182, 209, 211-212, 259-260, 282-285, 288, 332, 339-340, 342, 344-345,
  347-349, 351-357, 382-384, 440, 442-445, 466, 468, 556, 605, 607, 610-613, 665, 669, 706, 709
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.registration import (
    _k8s_config_is_empty,
    create_k8s_config,
    create_k8s_resolver,
    create_k8s_strategy,
    create_k8s_validator,
    get_k8s_extension_defaults,
    register_k8s_auth_strategies,
    register_k8s_extensions,
    register_k8s_provider_settings,
    register_k8s_template_factory,
)

# ---------------------------------------------------------------------------
# _k8s_config_is_empty
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestK8sConfigIsEmpty:
    def test_none_is_empty(self) -> None:
        assert _k8s_config_is_empty(None) is True

    def test_empty_dict_is_empty(self) -> None:
        assert _k8s_config_is_empty({}) is True

    def test_dict_with_only_in_cluster_false_is_empty(self) -> None:
        # in_cluster=False carries no useful targeting info
        assert _k8s_config_is_empty({"in_cluster": False}) is True

    def test_dict_with_in_cluster_true_is_not_empty(self) -> None:
        assert _k8s_config_is_empty({"in_cluster": True}) is False

    def test_dict_with_context_is_not_empty(self) -> None:
        assert _k8s_config_is_empty({"context": "my-ctx"}) is False

    def test_dict_with_kubeconfig_path_is_not_empty(self) -> None:
        assert _k8s_config_is_empty({"kubeconfig_path": "/etc/kube"}) is False

    def test_non_dict_returns_false(self) -> None:
        # Any non-dict, non-None object is treated as not empty
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        cfg = K8sProviderConfig(namespace="default")  # type: ignore[call-arg]
        assert _k8s_config_is_empty(cfg) is False


# ---------------------------------------------------------------------------
# create_k8s_config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateK8sConfig:
    def test_creates_config_from_dict(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        cfg = create_k8s_config({"namespace": "test"})
        assert isinstance(cfg, K8sProviderConfig)
        assert cfg.namespace == "test"

    def test_empty_dict_creates_default_config(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        cfg = create_k8s_config({})
        assert isinstance(cfg, K8sProviderConfig)


# ---------------------------------------------------------------------------
# create_k8s_resolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateK8sResolver:
    def test_returns_none(self) -> None:
        assert create_k8s_resolver() is None


# ---------------------------------------------------------------------------
# create_k8s_validator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateK8sValidator:
    def test_returns_validator_instance(self) -> None:
        validator = create_k8s_validator()
        assert validator is not None
        assert hasattr(validator, "validate")


# ---------------------------------------------------------------------------
# create_k8s_strategy — empty config raises RuntimeError (line 107-131)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateK8sStrategy:
    def test_empty_dict_raises_runtime_error(self) -> None:
        with pytest.raises(RuntimeError, match="explicit cluster-targeting"):
            create_k8s_strategy({})

    def test_none_raises_runtime_error(self) -> None:
        with pytest.raises(RuntimeError, match="explicit cluster-targeting"):
            create_k8s_strategy(None)

    def test_provider_instance_config_empty_raises(self) -> None:
        instance_cfg = MagicMock()
        instance_cfg.config = {}
        instance_cfg.name = "k8s-test"
        with pytest.raises(RuntimeError, match="explicit cluster-targeting"):
            create_k8s_strategy(instance_cfg)

    def test_valid_k8s_config_creates_strategy(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig
        from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

        cfg = K8sProviderConfig(namespace="test")  # type: ignore[call-arg]
        strategy = create_k8s_strategy(cfg)
        assert isinstance(strategy, K8sProviderStrategy)

    def test_dict_with_in_cluster_creates_strategy(self) -> None:
        from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

        strategy = create_k8s_strategy({"in_cluster": True})
        assert isinstance(strategy, K8sProviderStrategy)


# ---------------------------------------------------------------------------
# get_k8s_extension_defaults
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetK8sExtensionDefaults:
    def test_returns_dict(self) -> None:
        defaults = get_k8s_extension_defaults()
        assert isinstance(defaults, dict)


# ---------------------------------------------------------------------------
# register_k8s_auth_strategies — disabled path (line 330-337)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterK8sAuthStrategies:
    def test_disabled_logs_debug_and_returns(self) -> None:
        mock_logger = MagicMock()
        register_k8s_auth_strategies(logger=mock_logger, inbound_auth_enabled=False)
        mock_logger.debug.assert_called()

    def test_disabled_without_logger_does_not_raise(self) -> None:
        register_k8s_auth_strategies(logger=None, inbound_auth_enabled=False)

    def test_enabled_registers_strategy(self) -> None:
        mock_registry = MagicMock()
        mock_registry.is_registered.return_value = False
        mock_logger = MagicMock()
        with patch(
            "orb.infrastructure.auth.registry.get_auth_registry",
            return_value=mock_registry,
        ):
            register_k8s_auth_strategies(logger=mock_logger, inbound_auth_enabled=True)
        mock_registry.register_strategy.assert_called_once()

    def test_already_registered_is_idempotent(self) -> None:
        mock_registry = MagicMock()
        mock_registry.is_registered.return_value = True  # already registered
        with patch(
            "orb.infrastructure.auth.registry.get_auth_registry",
            return_value=mock_registry,
        ):
            # Should not raise, should not call register_strategy again
            register_k8s_auth_strategies(logger=None, inbound_auth_enabled=True)
        mock_registry.register_strategy.assert_not_called()


# ---------------------------------------------------------------------------
# register_k8s_template_factory (line 360-388)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterK8sTemplateFactory:
    def test_registers_template_class_with_factory(self) -> None:
        mock_factory = MagicMock()
        mock_logger = MagicMock()
        register_k8s_template_factory(mock_factory, logger=mock_logger)
        mock_factory.register_provider_template_class.assert_called_once()
        args = mock_factory.register_provider_template_class.call_args[0]
        assert args[0] == "k8s"

    def test_import_error_is_swallowed(self) -> None:
        import sys

        mock_factory = MagicMock()
        mock_logger = MagicMock()
        # Force the internal `from ...k8s_template_aggregate import K8sTemplate`
        # to raise ImportError by mapping the module to None in sys.modules.
        with patch.dict(
            sys.modules,
            {"orb.providers.k8s.domain.template.k8s_template_aggregate": None},
        ):
            # Must not raise — the ImportError branch is a defensive no-op.
            register_k8s_template_factory(mock_factory, logger=mock_logger)
        # The template class must NOT have been registered on the ImportError path.
        mock_factory.register_provider_template_class.assert_not_called()
        # The ImportError branch logs at debug level, not warning/error.
        mock_logger.debug.assert_called_once()
        mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# register_k8s_extensions (line 263-288)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterK8sExtensions:
    def test_registers_extension_successfully(self) -> None:
        mock_logger = MagicMock()
        # Should not raise
        register_k8s_extensions(logger=mock_logger)
        mock_logger.debug.assert_called()

    def test_registers_without_logger(self) -> None:
        register_k8s_extensions(logger=None)


# ---------------------------------------------------------------------------
# register_k8s_provider_settings (line 248-260)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterK8sProviderSettings:
    def test_does_not_raise(self) -> None:
        register_k8s_provider_settings()
