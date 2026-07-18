"""Unit tests for K8sCapabilityService.

Covers the uncovered lines listed in the gap file:
  capability_service.py :: 39,113,134,162,167-177,182,187-189,213-214,224,238-239,242,
                           258-261,270-271,275-279,281-282,284,286-290,292,298-299,303-304
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.base.strategy import ProviderCapabilities
from orb.providers.k8s.services.capability_service import (
    K8sCapabilityService,
    _normalise_sentinel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_service() -> K8sCapabilityService:
    return K8sCapabilityService(logger=_make_logger())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _normalise_sentinel
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormaliseSentinel:
    def test_hyphen_replaced_with_underscore(self) -> None:
        assert _normalise_sentinel("in-cluster") == "in_cluster"

    def test_uppercase_lowercased(self) -> None:
        assert _normalise_sentinel("IN-CLUSTER") == "in_cluster"

    def test_already_normalised_is_unchanged(self) -> None:
        assert _normalise_sentinel("in_cluster") == "in_cluster"

    def test_mixed_case_and_hyphens(self) -> None:
        assert _normalise_sentinel("In-Cluster") == "in_cluster"


# ---------------------------------------------------------------------------
# get_capabilities — line 39 constructor, line 52-96 body
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetCapabilities:
    def test_returns_provider_capabilities(self) -> None:
        svc = _make_service()
        caps = svc.get_capabilities()
        assert isinstance(caps, ProviderCapabilities)

    def test_provider_type_is_k8s(self) -> None:
        caps = _make_service().get_capabilities()
        assert caps.provider_type == "k8s"

    def test_supported_apis_includes_pod(self) -> None:
        caps = _make_service().get_capabilities()
        assert "Pod" in caps.supported_apis

    def test_selective_termination_by_api_pod_is_true(self) -> None:
        caps = _make_service().get_capabilities()
        assert caps.features["selective_termination_by_api"]["Pod"] is True

    def test_start_stop_supported_by_api_pod_is_false(self) -> None:
        caps = _make_service().get_capabilities()
        assert caps.features["start_stop_supported_by_api"]["Pod"] is False

    def test_start_stop_deployment_is_true(self) -> None:
        caps = _make_service().get_capabilities()
        assert caps.features["start_stop_supported_by_api"]["Deployment"] is True


# ---------------------------------------------------------------------------
# generate_provider_name (line 113)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGenerateProviderName:
    def test_with_context_uses_k8s_prefix(self) -> None:
        name = K8sCapabilityService.generate_provider_name({"context": "my-cluster"})
        assert name == "k8s_my-cluster"

    def test_no_context_returns_in_cluster(self) -> None:
        name = K8sCapabilityService.generate_provider_name({})
        assert name == "k8s_in-cluster"

    def test_context_special_chars_sanitised(self) -> None:
        name = K8sCapabilityService.generate_provider_name({"context": "arn:aws:eks:us/east"})
        assert name.startswith("k8s_")
        # Colons and slashes become hyphens
        assert ":" not in name
        assert "/" not in name

    def test_context_in_cluster_gets_ctx_prefix(self) -> None:
        # The literal "in-cluster" context would collide with the fallback — must be prefixed
        name = K8sCapabilityService.generate_provider_name({"context": "in-cluster"})
        assert "ctx_" in name


# ---------------------------------------------------------------------------
# parse_provider_name (line 119-124)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseProviderName:
    def test_valid_k8s_prefix_returns_suffix(self) -> None:
        result = K8sCapabilityService.parse_provider_name("k8s_my-cluster")
        assert result == {"context_or_namespace": "my-cluster"}

    def test_non_k8s_prefix_returns_empty(self) -> None:
        result = K8sCapabilityService.parse_provider_name("aws_us-east-1")
        assert result == {}


# ---------------------------------------------------------------------------
# get_provider_name_pattern / get_supported_apis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStaticHelpers:
    def test_get_provider_name_pattern(self) -> None:
        assert "k8s" in K8sCapabilityService.get_provider_name_pattern()

    def test_get_supported_apis_returns_list(self) -> None:
        apis = K8sCapabilityService.get_supported_apis()
        assert isinstance(apis, list)
        assert "Pod" in apis

    def test_get_available_regions_returns_empty(self) -> None:
        assert K8sCapabilityService.get_available_regions() == []

    def test_get_default_region_returns_empty_string(self) -> None:
        assert K8sCapabilityService.get_default_region() == ""


# ---------------------------------------------------------------------------
# CLI helpers (lines 134, 162, 167-177, 182, 187-189)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCLIHelpers:
    def test_get_cli_extra_config_keys(self) -> None:
        keys = K8sCapabilityService.get_cli_extra_config_keys()
        assert "context" in keys
        assert "namespace" in keys

    def test_get_cli_infrastructure_defaults_returns_empty(self) -> None:
        assert K8sCapabilityService.get_cli_infrastructure_defaults(None) == {}

    def test_get_cli_provider_config_with_all_args(self) -> None:
        args = SimpleNamespace(
            kubernetes_context="my-ctx",
            kubernetes_kubeconfig="/etc/kube",
            kubernetes_namespace="orb",
        )
        result = K8sCapabilityService.get_cli_provider_config(args)
        assert result["context"] == "my-ctx"
        assert result["kubeconfig_path"] == "/etc/kube"
        assert result["namespace"] == "orb"

    def test_get_cli_provider_config_missing_attrs_returns_empty(self) -> None:
        args = SimpleNamespace()
        result = K8sCapabilityService.get_cli_provider_config(args)
        assert result == {}

    def test_get_cli_provider_config_partial_attrs(self) -> None:
        args = SimpleNamespace(kubernetes_context="ctx", kubernetes_kubeconfig=None)
        # namespace attr missing entirely
        result = K8sCapabilityService.get_cli_provider_config(args)
        assert result["context"] == "ctx"
        assert "kubeconfig_path" not in result

    def test_get_operational_param_choices_returns_empty(self) -> None:
        assert K8sCapabilityService.get_operational_param_choices("whatever") == []

    def test_get_operational_param_default_namespace(self) -> None:
        assert K8sCapabilityService.get_operational_param_default("namespace") == "default"

    def test_get_operational_param_default_unknown(self) -> None:
        assert K8sCapabilityService.get_operational_param_default("other") == ""


# ---------------------------------------------------------------------------
# Credential helpers (lines 213-214, 224, 238-239, 242, 258-261, 270-271, 275-279, 281-282, 284,
# 286-290, 292, 298-299, 303-304)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAvailableCredentialSources:
    """Tests for the instance method get_available_credential_sources.

    The method detects in-cluster and enumerates kubeconfig contexts.
    We mock both kubernetes.config and the is_in_cluster helper.
    """

    def _make_fake_k8s_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        contexts: list[dict[str, Any]] | None = None,
        current: dict[str, Any] | None = None,
        raise_on_list: bool = False,
    ) -> None:
        """Inject a fake kubernetes.config into sys.modules."""
        if raise_on_list:
            fake_list = MagicMock(side_effect=RuntimeError("no kubeconfig"))
        else:
            fake_list = MagicMock(return_value=(contexts or [], current))

        fake_config = SimpleNamespace(list_kube_config_contexts=fake_list)
        monkeypatch.setitem(sys.modules, "kubernetes.config", fake_config)
        import kubernetes

        monkeypatch.setattr(kubernetes, "config", fake_config, raising=False)

    def test_in_cluster_source_when_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = _make_service()
        self._make_fake_k8s_config(monkeypatch, contexts=[], current=None, raise_on_list=True)
        with patch("orb.providers.k8s.auth.in_cluster.is_in_cluster", return_value=True):
            sources = svc.get_available_credential_sources()
        in_cluster_names = [s["name"] for s in sources]
        assert "in_cluster" in in_cluster_names

    def test_kubeconfig_contexts_enumerated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = _make_service()
        fake_contexts = [
            {"name": "prod", "context": {"cluster": "prod-cluster"}},
            {"name": "dev", "context": {"cluster": "dev-cluster"}},
        ]
        fake_current = {"name": "prod"}
        self._make_fake_k8s_config(monkeypatch, contexts=fake_contexts, current=fake_current)
        with patch("orb.providers.k8s.auth.in_cluster.is_in_cluster", return_value=False):
            sources = svc.get_available_credential_sources()
        names = [s["name"] for s in sources]
        assert "prod" in names
        assert "dev" in names

    def test_current_context_marked_in_description(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = _make_service()
        fake_contexts = [{"name": "prod", "context": {"cluster": "prod-cluster"}}]
        fake_current = {"name": "prod"}
        self._make_fake_k8s_config(monkeypatch, contexts=fake_contexts, current=fake_current)
        with patch("orb.providers.k8s.auth.in_cluster.is_in_cluster", return_value=False):
            sources = svc.get_available_credential_sources()
        prod_source = next(s for s in sources if s["name"] == "prod")
        assert "(current)" in prod_source["description"]

    def test_context_with_different_cluster_shows_arrow(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = _make_service()
        fake_contexts = [{"name": "my-ctx", "context": {"cluster": "remote-cluster"}}]
        fake_current = {"name": "other"}
        self._make_fake_k8s_config(monkeypatch, contexts=fake_contexts, current=fake_current)
        with patch("orb.providers.k8s.auth.in_cluster.is_in_cluster", return_value=False):
            sources = svc.get_available_credential_sources()
        ctx_source = next(s for s in sources if s["name"] == "my-ctx")
        assert "→" in ctx_source["description"]

    def test_context_matching_cluster_no_arrow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = _make_service()
        # When name == cluster, the label is just name (no arrow)
        fake_contexts = [{"name": "same", "context": {"cluster": "same"}}]
        fake_current = {"name": "other"}
        self._make_fake_k8s_config(monkeypatch, contexts=fake_contexts, current=fake_current)
        with patch("orb.providers.k8s.auth.in_cluster.is_in_cluster", return_value=False):
            sources = svc.get_available_credential_sources()
        ctx_source = next(s for s in sources if s["name"] == "same")
        assert "→" not in ctx_source["description"]

    def test_falls_back_to_default_when_no_contexts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = _make_service()
        self._make_fake_k8s_config(monkeypatch, contexts=[], current=None)
        with patch("orb.providers.k8s.auth.in_cluster.is_in_cluster", return_value=False):
            sources = svc.get_available_credential_sources()
        assert any(s["name"] == "default" for s in sources)

    def test_kubeconfig_exception_does_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = _make_service()
        self._make_fake_k8s_config(monkeypatch, raise_on_list=True)
        with patch("orb.providers.k8s.auth.in_cluster.is_in_cluster", return_value=False):
            sources = svc.get_available_credential_sources()
        # Falls back to default
        assert any(s["name"] == "default" for s in sources)

    def test_context_with_empty_name_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = _make_service()
        fake_contexts = [
            {"name": "", "context": {"cluster": "ghost"}},
            {"name": "valid", "context": {"cluster": "valid-cluster"}},
        ]
        self._make_fake_k8s_config(monkeypatch, contexts=fake_contexts, current=None)
        with patch("orb.providers.k8s.auth.in_cluster.is_in_cluster", return_value=False):
            sources = svc.get_available_credential_sources()
        names = [s["name"] for s in sources]
        assert "" not in names
        assert "valid" in names


@pytest.mark.unit
class TestTestCredentials:
    """Tests for the static test_credentials method."""

    def test_returns_failure_when_sdk_not_installed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When kubernetes SDK import raises ImportError, return failure dict."""
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in ("kubernetes.client", "kubernetes.config"):
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            result = K8sCapabilityService.test_credentials(None)
        assert result["success"] is False
        assert "kubernetes" in result["error"].lower() or "not installed" in result["error"].lower()

    def test_api_exception_returns_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ApiException from the probe is caught and returned as failure."""
        exceptions = pytest.importorskip(
            "kubernetes.client.exceptions",
            reason="kubernetes SDK not installed",
        )
        exc = exceptions.ApiException(status=401)
        exc.reason = "Unauthorized"

        with (
            patch("kubernetes.config.load_config"),
            patch(
                "kubernetes.client.CoreV1Api.get_api_resources",
                side_effect=exc,
            ),
            patch("kubernetes.client.api_client.ApiClient"),
        ):
            result = K8sCapabilityService.test_credentials(None)
        # The ApiException is caught and its status/reason are formatted into
        # the error message (capability_service.py:298-302).
        assert result["success"] is False
        assert "401" in result["error"]
        assert "Unauthorized" in result["error"]

    def test_generic_exception_returns_failure(self) -> None:
        """A non-ApiException during credential check returns failure dict."""
        with (
            patch("kubernetes.config.load_config", side_effect=RuntimeError("oops")),
        ):
            result = K8sCapabilityService.test_credentials(None)
        assert result["success"] is False
        assert "oops" in result["error"]

    def test_in_cluster_sentinel_uses_incluster_config(self) -> None:
        """Passing the in-cluster sentinel calls load_incluster_config."""
        with (
            patch("kubernetes.config.load_incluster_config") as mock_incluster,
            patch("kubernetes.config.load_config") as mock_load_config,
            patch("kubernetes.config.load_kube_config") as mock_kube_config,
            patch(
                "kubernetes.client.CoreV1Api.get_api_resources",
                return_value=MagicMock(resources=[]),
            ),
            patch(
                "kubernetes.client.api_client.ApiClient",
                return_value=MagicMock(configuration=MagicMock(host="https://k8s:6443")),
            ),
        ):
            result = K8sCapabilityService.test_credentials("in_cluster")
        # The sentinel routes to load_incluster_config (not the kubeconfig loaders)
        # and labels the context "in-cluster".
        mock_incluster.assert_called_once()
        mock_load_config.assert_not_called()
        mock_kube_config.assert_not_called()
        assert result["success"] is True
        assert result["context"] == "in-cluster"


@pytest.mark.unit
class TestGetCredentialRequirements:
    def test_returns_dict_with_known_keys(self) -> None:
        req = K8sCapabilityService.get_credential_requirements()
        assert "kubeconfig_path" in req
        assert "context" in req
        assert "namespace" in req

    def test_all_fields_have_required_key(self) -> None:
        for field_info in K8sCapabilityService.get_credential_requirements().values():
            assert "required" in field_info
            assert "description" in field_info


@pytest.mark.unit
class TestGetOperationalRequirements:
    def test_returns_namespace_entry(self) -> None:
        req = K8sCapabilityService.get_operational_requirements()
        assert "namespace" in req
        assert "description" in req["namespace"]
