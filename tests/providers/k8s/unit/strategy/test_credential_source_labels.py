"""Unit tests for K8sProviderStrategy.get_available_credential_sources labels.

Verifies the compact display format introduced to slim down the picker output
shown by ``orb init``:

* Context == cluster  -> label is just ``<context>``
* Context != cluster  -> label is ``<context> → <cluster>``
* Active context      -> ``(current)`` appended
* In-cluster          -> label is ``in-cluster ServiceAccount``
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kube_ctx(name: str, cluster: str) -> dict:
    return {"name": name, "context": {"cluster": cluster}}


def _sources_with_contexts(
    contexts: list[dict],
    current_name: str | None = None,
    *,
    in_cluster: bool = False,
) -> list[dict]:
    """Call the classmethod with fully mocked external dependencies.

    ``kubernetes.config.list_kube_config_contexts`` is patched directly on the
    already-loaded module object so the patch works even when ``kubernetes`` was
    imported by an earlier test module.  ``orb.providers.k8s.auth.in_cluster``
    is replaced in ``sys.modules`` (always a fresh lazy import inside the
    method body).
    """
    fake_current = {"name": current_name} if current_name else None

    fake_in_cluster_mod = MagicMock()
    fake_in_cluster_mod.is_in_cluster.return_value = in_cluster

    with (
        patch(
            "kubernetes.config.list_kube_config_contexts",
            return_value=(contexts, fake_current),
        ),
        patch.dict(
            sys.modules,
            {"orb.providers.k8s.auth.in_cluster": fake_in_cluster_mod},
        ),
    ):
        return K8sProviderStrategy.get_available_credential_sources()


# ---------------------------------------------------------------------------
# Tests: context == cluster (equal names)
# ---------------------------------------------------------------------------


class TestCredentialSourceLabelEqual:
    """When context name and cluster name are identical the label is just the name."""

    def test_arn_context_equal_cluster(self) -> None:
        arn = "arn:aws:eks:eu-west-1:686521096028:cluster/ms-karpenter"
        sources = _sources_with_contexts([_make_kube_ctx(arn, arn)])
        assert len(sources) == 1
        assert sources[0]["description"] == arn

    def test_simple_name_equal_cluster(self) -> None:
        sources = _sources_with_contexts([_make_kube_ctx("staging", "staging")])
        assert sources[0]["description"] == "staging"

    def test_equal_with_current_marker(self) -> None:
        arn = "arn:aws:eks:eu-west-1:686521096028:cluster/ms-karpenter"
        sources = _sources_with_contexts([_make_kube_ctx(arn, arn)], current_name=arn)
        assert sources[0]["description"] == f"{arn} (current)"


# ---------------------------------------------------------------------------
# Tests: context != cluster (different names - arrow format)
# ---------------------------------------------------------------------------


class TestCredentialSourceLabelDifferent:
    """When context name differs from cluster name the arrow format is used."""

    def test_short_context_long_cluster(self) -> None:
        ctx_name = "large-eks-1"
        cluster = "arn:aws:eks:eu-west-1:686521096028:cluster/large-eks-1"
        sources = _sources_with_contexts([_make_kube_ctx(ctx_name, cluster)])
        assert sources[0]["description"] == f"{ctx_name} → {cluster}"

    def test_different_short_names(self) -> None:
        sources = _sources_with_contexts([_make_kube_ctx("my-ctx", "my-cluster")])
        assert sources[0]["description"] == "my-ctx → my-cluster"

    def test_different_with_current_marker(self) -> None:
        ctx_name = "large-eks-1"
        cluster = "arn:aws:eks:eu-west-1:686521096028:cluster/large-eks-1"
        sources = _sources_with_contexts(
            [_make_kube_ctx(ctx_name, cluster)],
            current_name=ctx_name,
        )
        assert sources[0]["description"] == f"{ctx_name} → {cluster} (current)"

    def test_unicode_arrow_not_em_dash(self) -> None:
        sources = _sources_with_contexts([_make_kube_ctx("ctx", "cluster")])
        desc = sources[0]["description"]
        assert "→" in desc  # Unicode right arrow U+2192
        assert "—" not in desc  # no em-dash
        assert "->" not in desc  # no ASCII arrow


# ---------------------------------------------------------------------------
# Tests: multiple contexts, current-marker on only one
# ---------------------------------------------------------------------------


class TestCredentialSourceCurrentMarker:
    """(current) appears on exactly the active context, nowhere else."""

    def test_only_one_current_marker(self) -> None:
        ctxs = [
            _make_kube_ctx("ctx-a", "ctx-a"),
            _make_kube_ctx("ctx-b", "ctx-b"),
            _make_kube_ctx("ctx-c", "ctx-c"),
        ]
        sources = _sources_with_contexts(ctxs, current_name="ctx-b")
        current_count = sum(1 for s in sources if "(current)" in s["description"])
        assert current_count == 1
        assert "(current)" in sources[1]["description"]

    def test_no_current_when_none_active(self) -> None:
        ctxs = [_make_kube_ctx("ctx-a", "ctx-a"), _make_kube_ctx("ctx-b", "ctx-b")]
        sources = _sources_with_contexts(ctxs, current_name=None)
        assert all("(current)" not in s["description"] for s in sources)


# ---------------------------------------------------------------------------
# Tests: in-cluster ServiceAccount label
# ---------------------------------------------------------------------------


class TestCredentialSourceInClusterLabel:
    """In-cluster entry uses the compact label."""

    def test_in_cluster_label(self) -> None:
        sources = _sources_with_contexts([], in_cluster=True)
        # Only the in-cluster entry should be present (no kubeconfig contexts)
        in_cluster_sources = [s for s in sources if s.get("name") == "in_cluster"]
        assert len(in_cluster_sources) == 1
        assert in_cluster_sources[0]["description"] == "in-cluster ServiceAccount"

    def test_no_old_verbose_in_cluster_text(self) -> None:
        sources = _sources_with_contexts([], in_cluster=True)
        for s in sources:
            assert "token mounted at" not in s.get("description", "")
            assert "/var/run/secrets" not in s.get("description", "")


# ---------------------------------------------------------------------------
# Tests: no "kubeconfig context" prefix anywhere
# ---------------------------------------------------------------------------


class TestCredentialSourceNoOldPrefix:
    """Old 'kubeconfig context' prefix and '-> cluster' syntax are gone."""

    def test_no_kubeconfig_context_prefix(self) -> None:
        ctxs = [_make_kube_ctx("ctx", "cluster")]
        sources = _sources_with_contexts(ctxs)
        for s in sources:
            assert "kubeconfig context" not in s.get("description", "")

    def test_no_ascii_arrow_cluster_suffix(self) -> None:
        ctxs = [_make_kube_ctx("ctx", "cluster")]
        sources = _sources_with_contexts(ctxs)
        for s in sources:
            assert "-> cluster" not in s.get("description", "")

    def test_no_quoted_context_name(self) -> None:
        """Old format used single quotes around the context name - that is gone."""
        ctxs = [_make_kube_ctx("my-ctx", "my-cluster")]
        sources = _sources_with_contexts(ctxs)
        for s in sources:
            assert "'my-ctx'" not in s.get("description", "")
