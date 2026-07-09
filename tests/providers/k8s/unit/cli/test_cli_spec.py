"""Unit tests for ``K8sCLISpec``."""

from __future__ import annotations

import argparse

import pytest

from orb.providers.base.provider_cli_spec_port import ProviderCLISpecPort
from orb.providers.k8s.cli.k8s_cli_spec import K8sCLISpec


def _args(
    namespace: str | None = None,
    kubeconfig: str | None = None,
    context: str | None = None,
) -> argparse.Namespace:
    ns = argparse.Namespace()
    ns.kubernetes_namespace = namespace
    ns.kubernetes_kubeconfig = kubeconfig
    ns.kubernetes_context = context
    return ns


@pytest.fixture()
def spec() -> K8sCLISpec:
    return K8sCLISpec()


def test_satisfies_provider_cli_spec_port(spec: K8sCLISpec) -> None:
    """The CLI spec satisfies the runtime-checkable port protocol."""
    assert isinstance(spec, ProviderCLISpecPort)


def test_add_arguments_registers_namespace_and_kubeconfig(spec: K8sCLISpec) -> None:
    """``add_arguments`` installs the kubernetes-specific flags on the parser."""
    parser = argparse.ArgumentParser()
    spec.add_arguments(parser)

    parsed = parser.parse_args(
        [
            "--namespace",
            "orb-prod",
            "--kubeconfig",
            "/home/user/.kube/config",
            "--kube-context",
            "minikube",
        ]
    )
    assert parsed.kubernetes_namespace == "orb-prod"
    assert parsed.kubernetes_kubeconfig == "/home/user/.kube/config"
    assert parsed.kubernetes_context == "minikube"


def test_add_arguments_defaults_to_none(spec: K8sCLISpec) -> None:
    """When flags are omitted, argparse populates them as ``None``."""
    parser = argparse.ArgumentParser()
    spec.add_arguments(parser)

    parsed = parser.parse_args([])
    assert parsed.kubernetes_namespace is None
    assert parsed.kubernetes_kubeconfig is None
    assert parsed.kubernetes_context is None


def test_extract_config_returns_full_dict(spec: K8sCLISpec) -> None:
    """``extract_config`` materialises every field for the add command."""
    cfg = spec.extract_config(_args(namespace="orb", kubeconfig="/tmp/kubeconfig", context="dev"))
    assert cfg == {
        "namespace": "orb",
        "kubeconfig_path": "/tmp/kubeconfig",
        "context": "dev",
    }


def test_extract_config_defaults_namespace_to_default(spec: K8sCLISpec) -> None:
    """``extract_config`` falls back to the kube-API default namespace."""
    cfg = spec.extract_config(_args())
    assert cfg["namespace"] == "default"
    assert cfg["kubeconfig_path"] is None
    assert cfg["context"] is None


def test_extract_partial_config_only_returns_explicit(spec: K8sCLISpec) -> None:
    """``extract_partial_config`` skips unset fields so updates are minimal."""
    partial = spec.extract_partial_config(_args(namespace="orb"))
    assert partial == {"namespace": "orb"}

    partial = spec.extract_partial_config(_args(kubeconfig="/tmp/cfg"))
    assert partial == {"kubeconfig_path": "/tmp/cfg"}

    partial = spec.extract_partial_config(_args())
    assert partial == {}


def test_validate_add_returns_empty_list(spec: K8sCLISpec) -> None:
    """The kubernetes provider has no strictly-required CLI fields."""
    assert spec.validate_add(_args()) == []
    assert spec.validate_add(_args(namespace="orb")) == []


def test_generate_name_basic(spec: K8sCLISpec) -> None:
    """Context + namespace produce the expected provider instance name."""
    name = spec.generate_name(_args(namespace="orb-prod", context="minikube"))
    assert name == "kubernetes_minikube_orb-prod"


def test_generate_name_special_chars_sanitised(spec: K8sCLISpec) -> None:
    """Special characters in the context / namespace are replaced with hyphens."""
    name = spec.generate_name(_args(namespace="orb.team@org", context="arn:aws:eks:context"))
    assert name == "kubernetes_arn-aws-eks-context_orb-team-org"


def test_generate_name_omitted_namespace_defaults(spec: K8sCLISpec) -> None:
    """When the namespace is omitted, the name still resolves cleanly."""
    name = spec.generate_name(_args(context="dev"))
    assert name == "kubernetes_dev_default"


def test_format_display_returns_label_value_pairs(spec: K8sCLISpec) -> None:
    """``format_display`` produces the (label, value) pairs the CLI prints."""
    pairs = spec.format_display(
        {
            "namespace": "orb-prod",
            "kubeconfig_path": "/etc/kubeconfig",
            "context": "dev",
        }
    )
    assert ("Namespace", "orb-prod") in pairs
    assert ("Kubeconfig", "/etc/kubeconfig") in pairs
    assert ("Context", "dev") in pairs


def test_format_display_uses_placeholder_for_missing(spec: K8sCLISpec) -> None:
    """Missing config values are rendered as the unicode placeholder character."""
    pairs = dict(spec.format_display({}))
    # The placeholder is a single character: we just check the label exists and
    # carries some non-empty rendering rather than asserting on the exact glyph.
    assert pairs["Namespace"]
    assert pairs["Kubeconfig"]
    assert pairs["Context"]


def test_registered_with_cli_spec_registry() -> None:
    """``initialize_k8s_provider`` registers the CLI spec under 'k8s'."""
    from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
    from orb.providers.k8s.registration import initialize_k8s_provider

    initialize_k8s_provider()
    registered = CLISpecRegistry.get("k8s")
    assert isinstance(registered, K8sCLISpec)
