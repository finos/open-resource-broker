"""Config and init parity across providers, exercised through the real registry.

Every provider is registered through the production registration path and its
config factory is driven the way startup drives it.  The unknown-field policy
of each provider config is stated explicitly in ``EXTRA_POLICY`` so a change is
a visible edit rather than a silent drift.  Known deviations are strict xfails
that reference the tracking issue: they turn into failures (and must be
removed) the moment the behaviour is fixed.
"""

from __future__ import annotations

from typing import Any

import pytest

from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig
from orb.providers.base.strategy.provider_strategy import ProviderStrategy

PROVIDERS = ("aws", "azure", "gcp", "k8s", "oci")

# Unknown-field policy of each provider config model.
EXTRA_POLICY = {
    "aws": "allow",
    "azure": "forbid",
    "gcp": "forbid",
    "k8s": "forbid",
    "oci": "allow",
}

REALISTIC_CONFIG: dict[str, dict[str, Any]] = {
    "aws": {"region": "us-east-1", "profile": "default"},
    "azure": {
        "subscription_id": "00000000-0000-0000-0000-000000000000",
        "location": "eastus",
    },
    "gcp": {"project_id": "orb-example-12345", "region": "us-central1"},
    "k8s": {"context": "kind-orb"},
    "oci": {"region": "us-ashburn-1"},
}


def _tracked(provider: str, deviating: set[str], issue: str, reason: str) -> Any:
    marks = []
    if provider in deviating:
        marks.append(pytest.mark.xfail(strict=True, reason=f"{issue}: {reason}"))
    return pytest.param(provider, id=provider, marks=marks)


def test_registry_covers_parity_providers(registry) -> None:
    assert sorted(registry.get_registered_providers()) == sorted(PROVIDERS)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_extra_policy_is_as_declared(registry, provider) -> None:
    config = registry.create_config(provider, dict(REALISTIC_CONFIG[provider]))
    assert type(config).model_config.get("extra") == EXTRA_POLICY[provider]


# The type discriminator selects which provider to build; it is not a config field.
@pytest.mark.parametrize(
    "provider",
    [
        _tracked("aws", {"aws"}, "#729", "aws config retains the discriminator as an extra field"),
        _tracked("azure", {"azure"}, "#729", "azure config rejects the discriminator"),
        _tracked("gcp", {"gcp"}, "#729", "gcp config rejects the discriminator"),
        _tracked("k8s", set(), "", ""),
        _tracked("oci", {"oci"}, "#729", "oci config retains the discriminator as an extra field"),
    ],
)
def test_discriminator_is_accepted_and_not_retained(registry, provider) -> None:
    data = {**REALISTIC_CONFIG[provider], "type": provider}
    config = registry.create_config(provider, data)

    assert "type" not in (getattr(config, "model_extra", None) or {})
    assert not hasattr(config, "type")


@pytest.mark.parametrize(
    "provider",
    [
        _tracked("aws", {"aws"}, "#730", "aws factory cannot take a ProviderInstanceConfig"),
        _tracked("azure", set(), "", ""),
        _tracked("gcp", set(), "", ""),
        _tracked("k8s", {"k8s"}, "#730", "k8s factory cannot take a ProviderInstanceConfig"),
        _tracked("oci", set(), "", ""),
    ],
)
def test_factory_accepts_provider_instance_config(registry, provider) -> None:
    """ProviderConfigBuilder hands the whole instance config to the factory."""
    instance = ProviderInstanceConfig.model_validate(
        {"name": f"{provider}-1", "type": provider, "config": dict(REALISTIC_CONFIG[provider])}
    )
    config = registry.get_config_factory(provider)(instance)

    assert config is not None


@pytest.mark.parametrize("provider", PROVIDERS)
def test_strategy_resolves_through_registry(registry, provider) -> None:
    assert registry.is_provider_registered(provider)
    strategy_class = registry._get_type_registration(provider).strategy_class
    assert isinstance(strategy_class, type) and issubclass(strategy_class, ProviderStrategy)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_init_helpers_are_callable(registry, provider) -> None:
    strategy_class = registry._get_type_registration(provider).strategy_class

    assert callable(strategy_class.get_available_credential_sources)
    assert callable(strategy_class.test_credentials)
    assert isinstance(strategy_class.get_credential_requirements(), dict)
    assert isinstance(strategy_class.get_operational_requirements(), dict)
