"""Unit tests for :mod:`orb.providers.k8s.handler_plugins`.

A handler registered with :func:`register_k8s_handler` must be visible to
every place that lists the Kubernetes provider's supported APIs: new
strategies, the template validator, the template adapter, the capability
service and the provider defaults.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.defaults_loader import KubernetesDefaultsLoader
from orb.providers.k8s.handler_plugins import (
    plugin_handler_defaults,
    register_k8s_handler,
    registered_k8s_handlers,
    supported_provider_apis,
    unregister_k8s_handler,
)
from orb.providers.k8s.infrastructure.adapters.template_adapter import K8sTemplateAdapter
from orb.providers.k8s.services.capability_service import K8sCapabilityService
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy
from orb.providers.k8s.validation.template_validator import K8sTemplateValidator

_API = "KubernetesPluginTest"


class _StubHandler:
    """Stands in for a handler class; never constructed here."""


class _OtherStubHandler:
    """A different handler class, for the conflict path."""


@pytest.fixture(autouse=True)
def _clean_registry() -> Iterator[None]:
    unregister_k8s_handler(_API)
    yield
    unregister_k8s_handler(_API)


def _make_strategy() -> K8sProviderStrategy:
    return K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_supported_apis_are_the_built_ins_when_nothing_is_registered() -> None:
    assert supported_provider_apis() == ["Pod", "Deployment", "StatefulSet", "Job"]


def test_register_adds_the_api_after_the_built_ins() -> None:
    register_k8s_handler(_API, _StubHandler)
    assert supported_provider_apis() == ["Pod", "Deployment", "StatefulSet", "Job", _API]
    assert registered_k8s_handlers() == {_API: _StubHandler}


def test_register_is_idempotent_for_the_same_class() -> None:
    register_k8s_handler(_API, _StubHandler)
    register_k8s_handler(_API, _StubHandler)
    assert registered_k8s_handlers() == {_API: _StubHandler}


def test_register_rejects_a_different_class() -> None:
    register_k8s_handler(_API, _StubHandler)
    with pytest.raises(ValueError, match="already registered"):
        register_k8s_handler(_API, _OtherStubHandler)


@pytest.mark.parametrize("api", ["Pod", "Deployment", "StatefulSet", "Job"])
def test_register_rejects_a_built_in_api(api: str) -> None:
    with pytest.raises(ValueError, match="built-in"):
        register_k8s_handler(api, _StubHandler)


def test_register_rejects_an_empty_api() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        register_k8s_handler("", _StubHandler)


def test_unregister_removes_the_api() -> None:
    register_k8s_handler(_API, _StubHandler)
    unregister_k8s_handler(_API)
    assert _API not in supported_provider_apis()
    unregister_k8s_handler(_API)  # unknown name is a no-op


# ---------------------------------------------------------------------------
# Consumers
# ---------------------------------------------------------------------------


def test_new_strategy_gets_the_registered_handler() -> None:
    register_k8s_handler(_API, _StubHandler)
    strategy = _make_strategy()
    assert strategy._handler_factories[_API] is _StubHandler
    assert _API in strategy.get_supported_apis()


def test_strategy_lists_handlers_registered_on_the_instance() -> None:
    strategy = _make_strategy()
    strategy.register_handler("KubernetesInstanceOnly", _StubHandler)
    assert "KubernetesInstanceOnly" in strategy.get_supported_apis()
    assert "KubernetesInstanceOnly" not in _make_strategy().get_supported_apis()


def test_strategy_get_supported_apis_works_unbound() -> None:
    # ProviderRegistry.list_all_provider_apis calls it with the class as self.
    register_k8s_handler(_API, _StubHandler)
    apis = K8sProviderStrategy.get_supported_apis(K8sProviderStrategy)  # type: ignore[arg-type]
    assert apis == ["Pod", "Deployment", "StatefulSet", "Job", _API]


def test_validator_accepts_a_registered_api() -> None:
    validator = K8sTemplateValidator()
    template = SimpleNamespace(provider_api=_API)
    assert validator._check_provider_api(template) != []
    register_k8s_handler(_API, _StubHandler)
    assert validator._check_provider_api(template) == []


def test_template_adapter_accepts_a_registered_api() -> None:
    adapter = MagicMock(spec=K8sTemplateAdapter)
    template = SimpleNamespace(provider_api=_API)
    assert K8sTemplateAdapter._validate_provider_api(adapter, template) != []  # type: ignore[arg-type]
    register_k8s_handler(_API, _StubHandler)
    assert K8sTemplateAdapter._validate_provider_api(adapter, template) == []  # type: ignore[arg-type]
    assert _API in K8sTemplateAdapter.get_supported_provider_apis(adapter)


def test_capability_service_lists_a_registered_api() -> None:
    register_k8s_handler(_API, _StubHandler)
    assert _API in K8sCapabilityService.get_supported_apis()


def test_defaults_loader_adds_a_handler_entry() -> None:
    register_k8s_handler(_API, _StubHandler, supports_spot=True)
    handlers = KubernetesDefaultsLoader().load_defaults()["provider"]["provider_defaults"]["k8s"][
        "handlers"
    ]
    assert handlers[_API] == {
        "handler_class": "_StubHandler",
        "supports_spot": True,
        "supports_ondemand": True,
    }
    # The built-in entries from k8s_defaults.json are untouched.
    assert handlers["Pod"]["handler_class"] == "K8sPodHandler"


def test_defaults_loader_is_unchanged_without_plugins() -> None:
    assert plugin_handler_defaults() == {}
    handlers = KubernetesDefaultsLoader().load_defaults()["provider"]["provider_defaults"]["k8s"][
        "handlers"
    ]
    assert list(handlers) == ["Pod", "Deployment", "StatefulSet", "Job"]
