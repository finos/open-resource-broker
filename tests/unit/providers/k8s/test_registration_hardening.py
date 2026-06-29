"""Unit tests for k8s registration hardening.

Covers:
- create_k8s_strategy rejects None / empty-dict configs to prevent silent wrong-cluster targeting
- register_k8s_provider_instance logs ERROR with instance name + config snippet on failure
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.registration import (
    _k8s_config_is_empty,
    create_k8s_strategy,
    register_k8s_provider_instance,
)

# ---------------------------------------------------------------------------
# _k8s_config_is_empty helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, True),
        ({}, True),
        ({"namespace": "orb"}, True),  # namespace alone is not cluster-targeting
        ({"in_cluster": False}, True),  # explicit False offers no target
        ({"kubeconfig_path": "/tmp/k"}, False),
        ({"context": "my-cluster"}, False),
        ({"in_cluster": True}, False),  # explicit in-cluster opt-in is valid
        ({"kubeconfig_path": "/tmp/k", "context": "ctx"}, False),
    ],
)
def test_k8s_config_is_empty(value, expected) -> None:
    assert _k8s_config_is_empty(value) == expected


# ---------------------------------------------------------------------------
# create_k8s_strategy rejects empty config
# ---------------------------------------------------------------------------


def test_create_k8s_strategy_raises_for_none_config() -> None:
    """None config must raise RuntimeError to prevent connecting to an ambient cluster."""
    with pytest.raises(RuntimeError, match="cluster-targeting"):
        create_k8s_strategy(None)


def test_create_k8s_strategy_raises_for_empty_dict() -> None:
    """Empty dict config must raise RuntimeError for the same reason."""
    with pytest.raises(RuntimeError, match="cluster-targeting"):
        create_k8s_strategy({})


def test_create_k8s_strategy_raises_for_namespace_only() -> None:
    """A dict with only namespace (no kubeconfig_path / context) must raise."""
    with pytest.raises(RuntimeError, match="cluster-targeting"):
        create_k8s_strategy({"namespace": "orb-system"})


def test_create_k8s_strategy_proceeds_with_kubeconfig_path() -> None:
    """A dict that sets kubeconfig_path must not be rejected by the empty-config guard.

    The strategy may or may not succeed depending on the environment, but it
    must never raise the empty-config sentinel.
    """
    try:
        create_k8s_strategy({"kubeconfig_path": "/nonexistent/config"})
    except RuntimeError as exc:
        assert "cluster-targeting" not in str(exc), (
            "empty-config guard must not fire when kubeconfig_path is set"
        )


def test_create_k8s_strategy_proceeds_with_context() -> None:
    """A dict that sets context must not be rejected by the empty-config guard."""
    try:
        create_k8s_strategy({"context": "my-cluster"})
    except RuntimeError as exc:
        assert "cluster-targeting" not in str(exc), (
            "empty-config guard must not fire when context is set"
        )


def test_create_k8s_strategy_proceeds_with_in_cluster_true() -> None:
    """A dict with in_cluster=True must not be rejected by the empty-config guard."""
    try:
        create_k8s_strategy({"in_cluster": True})
    except RuntimeError as exc:
        assert "cluster-targeting" not in str(exc), (
            "empty-config guard must not fire when in_cluster=True is set"
        )


def test_create_k8s_strategy_rejects_in_cluster_false() -> None:
    """A dict with only in_cluster=False provides no useful targeting; must raise."""
    with pytest.raises(RuntimeError, match="cluster-targeting"):
        create_k8s_strategy({"in_cluster": False})


def test_create_k8s_strategy_proceeds_with_k8s_provider_config() -> None:
    """A K8sProviderConfig instance must bypass the empty-config guard entirely."""
    from orb.providers.k8s.configuration.config import K8sProviderConfig

    cfg = K8sProviderConfig(namespace="orb")

    # No RuntimeError about cluster-targeting — the K8sProviderConfig path
    # skips the guard. Strategy init may succeed or fail depending on the
    # environment, but must not raise the empty-config message.
    try:
        create_k8s_strategy(cfg)
    except RuntimeError as exc:
        assert "cluster-targeting" not in str(exc), (
            "empty-config guard must not fire for a K8sProviderConfig instance"
        )


# ---------------------------------------------------------------------------
# register_k8s_provider_instance — rich error logging
# ---------------------------------------------------------------------------


def _make_provider_instance(name="k8s-prod", config=None):
    instance = MagicMock()
    instance.name = name
    instance.config = config or {}
    return instance


def test_register_k8s_provider_instance_logs_error_with_instance_name(caplog) -> None:
    """When registration fails, the ERROR log must include the instance name."""
    instance = _make_provider_instance(
        name="k8s-prod",
        config={"kubeconfig_path": "/bad/path", "context": "bad-ctx", "namespace": "orb"},
    )

    logger = MagicMock()

    with patch(
        "orb.providers.registry.get_provider_registry",
        side_effect=RuntimeError("registry exploded"),
    ):
        result = register_k8s_provider_instance(instance, logger=logger)

    assert result is False
    logger.error.assert_called_once()
    call_args = logger.error.call_args
    # Format string is the first positional arg; remaining positional args fill %s/%r
    all_args = call_args.args
    assert any("k8s-prod" in str(a) for a in all_args), (
        f"Instance name 'k8s-prod' not found in error call args: {all_args}"
    )


def test_register_k8s_provider_instance_logs_error_with_config_keys() -> None:
    """The ERROR log must include kubeconfig_path, context, and namespace values."""
    instance = _make_provider_instance(
        name="k8s-staging",
        config={
            "kubeconfig_path": "/home/user/.kube/staging",
            "context": "staging-ctx",
            "namespace": "staging-ns",
        },
    )

    logger = MagicMock()

    with patch(
        "orb.providers.registry.get_provider_registry",
        side_effect=RuntimeError("boom"),
    ):
        register_k8s_provider_instance(instance, logger=logger)

    logger.error.assert_called_once()
    call_args = logger.error.call_args
    all_args = call_args.args

    # The config values must appear somewhere in the positional args
    joined = " ".join(str(a) for a in all_args)
    assert "/home/user/.kube/staging" in joined, "kubeconfig_path value missing from error log"
    assert "staging-ctx" in joined, "context value missing from error log"
    assert "staging-ns" in joined, "namespace value missing from error log"


def test_register_k8s_provider_instance_logs_error_with_exc_info() -> None:
    """The ERROR call must pass exc_info=True so the traceback is captured."""
    instance = _make_provider_instance(name="k8s-fail")
    logger = MagicMock()

    with patch(
        "orb.providers.registry.get_provider_registry",
        side_effect=RuntimeError("failure"),
    ):
        register_k8s_provider_instance(instance, logger=logger)

    logger.error.assert_called_once()
    kwargs = logger.error.call_args.kwargs
    assert kwargs.get("exc_info") is True, (
        "exc_info=True must be passed so the traceback appears in the log"
    )


def test_register_k8s_provider_instance_returns_false_on_error() -> None:
    """Registration must return False (not raise) so one bad instance does not
    abort the whole startup sequence."""
    instance = _make_provider_instance(name="k8s-bad")

    with patch(
        "orb.providers.registry.get_provider_registry",
        side_effect=RuntimeError("network down"),
    ):
        result = register_k8s_provider_instance(instance, logger=None)

    assert result is False
