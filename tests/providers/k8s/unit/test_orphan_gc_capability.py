"""The ``deletes_pods_without_request_record`` capability follows the real config chain.

The strategy is never built from a hand-made ``K8sProviderConfig``: each case configures
a provider instance, registers the k8s type in a registry and reads the capability from
the strategy both ways the product builds one: the cached daemon path and the throwaway
one the admin cleanup falls back to.
"""

from __future__ import annotations

import pytest

from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig
from orb.providers.k8s.registration import register_k8s_provider
from orb.providers.registry import ProviderRegistry

FEATURE = "deletes_pods_without_request_record"
ALL_ON = {
    "in_cluster": True,
    "orphan_gc_enabled": True,
    "auto_cleanup_orphans": True,
    "orb_instance_id": "orb-a",
}


def _instance(**config: object) -> ProviderInstanceConfig:
    return ProviderInstanceConfig(
        name="k8s-test", type="k8s", config={"in_cluster": True, **config}
    )


def _features(instance: ProviderInstanceConfig) -> tuple[bool, bool]:
    registry = ProviderRegistry()
    register_k8s_provider(registry)
    cached = registry.get_or_create_strategy(instance.name, instance)
    throwaway = registry.create_strategy_by_type("k8s", instance)
    assert cached is not None and throwaway is not None
    return (
        bool(cached.get_capabilities().features[FEATURE]),
        bool(throwaway.get_capabilities().features[FEATURE]),
    )


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (ALL_ON, True),
        ({**ALL_ON, "orphan_gc_enabled": False}, False),
        ({**ALL_ON, "auto_cleanup_orphans": False}, False),
        ({**ALL_ON, "orb_instance_id": ""}, False),
        ({"in_cluster": True}, False),
    ],
)
def test_true_only_when_all_three_settings_are_set(config, expected) -> None:
    assert _features(_instance(**config)) == (expected, expected)


def test_environment_layer_reaches_the_strategy(monkeypatch) -> None:
    monkeypatch.setenv("ORB_K8S_AUTO_CLEANUP_ORPHANS", "true")
    config = {k: v for k, v in ALL_ON.items() if k != "auto_cleanup_orphans"}

    assert _features(_instance(**config)) == (True, True)


def test_instance_overrides_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("ORB_K8S_AUTO_CLEANUP_ORPHANS", "true")

    assert _features(_instance(**{**ALL_ON, "auto_cleanup_orphans": False})) == (False, False)


def test_environment_does_not_override_the_instance(monkeypatch) -> None:
    monkeypatch.setenv("ORB_K8S_ORPHAN_GC_ENABLED", "false")

    assert _features(_instance(**ALL_ON)) == (True, True)
