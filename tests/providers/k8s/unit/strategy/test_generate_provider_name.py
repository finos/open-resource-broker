"""Unit tests for K8sProviderStrategy.generate_provider_name and related fixes."""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy


def _make_strategy(context: str | None = None) -> K8sProviderStrategy:
    config = K8sProviderConfig(context=context)
    return K8sProviderStrategy(
        config=config,
        logger=MagicMock(),
        kubernetes_client=MagicMock(),
    )


class TestK8sGenerateProviderName:
    def test_k8s_generate_provider_name_from_context(self) -> None:
        strategy = _make_strategy()
        name = strategy.generate_provider_name({"context": "ms-karpenter"})
        assert name == "k8s_ms-karpenter"

    def test_k8s_generate_provider_name_in_cluster_case(self) -> None:
        strategy = _make_strategy()
        # Empty context and no profile — in-cluster fallback
        name = strategy.generate_provider_name({})
        assert name == "k8s_in-cluster"

    def test_k8s_generate_provider_name_in_cluster_explicit(self) -> None:
        # profile key is dead; no-context config falls back to in-cluster sentinel.
        strategy = _make_strategy()
        name = strategy.generate_provider_name({})
        assert name == "k8s_in-cluster"

    def test_k8s_generate_provider_name_sanitizes_arn_context(self) -> None:
        strategy = _make_strategy()
        arn_context = "arn:aws:eks:eu-west-1:686521096028:cluster/ms-karpenter"
        name = strategy.generate_provider_name({"context": arn_context})
        assert name == "k8s_arn-aws-eks-eu-west-1-686521096028-cluster-ms-karpenter"

    def test_k8s_generate_provider_name_profile_key_ignored(self) -> None:
        """The legacy profile key is ignored; a bare profile dict falls back to in-cluster."""
        strategy = _make_strategy()
        name = strategy.generate_provider_name({"profile": "staging-cluster"})
        assert name == "k8s_in-cluster"

    def test_k8s_generate_provider_name_context_wins_over_profile(self) -> None:
        """context key wins; profile is not consulted."""
        strategy = _make_strategy()
        name = strategy.generate_provider_name({"context": "prod", "profile": "dev"})
        assert name == "k8s_prod"

    def test_k8s_generate_provider_name_sanitizes_slashes(self) -> None:
        strategy = _make_strategy()
        name = strategy.generate_provider_name({"context": "cluster/my-cluster"})
        assert name == "k8s_cluster-my-cluster"

    def test_k8s_generate_provider_name_starts_with_k8s_prefix(self) -> None:
        strategy = _make_strategy()
        name = strategy.generate_provider_name({"context": "anything"})
        assert name.startswith("k8s_")

    def test_k8s_parse_provider_name_round_trips(self) -> None:
        strategy = _make_strategy()
        name = strategy.generate_provider_name({"context": "my-context"})
        parsed = strategy.parse_provider_name(name)
        assert parsed["context_or_namespace"] == "my-context"

    def test_k8s_parse_provider_name_unknown_prefix_returns_empty(self) -> None:
        strategy = _make_strategy()
        assert strategy.parse_provider_name("kubernetes_old-name") == {}
