"""Unit tests for K8sInfrastructureDiscoveryService.validate_infrastructure.

All kubernetes SDK calls are mocked — no live cluster required.  The test
classes mirror the ticket acceptance criteria exactly:

* All checks pass → ``valid=True``, ``issues=[]``
* API server unreachable → ``valid=False``, issue contains
  ``"Apiserver unreachable at <endpoint>: <error>"``
* Context not found → ``valid=False``, issue contains
  ``"Configured context '<X>' not found in kubeconfig"``
* Namespace not found → ``valid=False``, issue contains
  ``"Namespace '<X>' not found in cluster"``
* ServiceAccount not found → ``valid=False``, issue contains
  ``"ServiceAccount '<X>' not found in namespace '<Y>'"``
* RBAC denied → ``valid=False``, issue contains
  ``"Missing permission: pods.<verb> in namespace <X>"``
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_exceptions import K8sDiscoveryError
from orb.providers.k8s.services.discovery_models import RBACProbeResult
from orb.providers.k8s.services.infrastructure_discovery_service import (
    K8sInfrastructureDiscoveryService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_ENDPOINT = "https://10.0.0.1:6443"


def _make_service(
    namespace: str = "default",
    context: str | None = None,
    in_cluster: bool | None = None,
    api_client: object | None = None,
) -> K8sInfrastructureDiscoveryService:
    """Construct a service with an injected logger (and optional api_client)."""
    config = K8sProviderConfig(
        namespace=namespace,
        context=context,
        in_cluster=in_cluster,
    )
    logger = MagicMock()
    client = api_client if api_client is not None else MagicMock()
    return K8sInfrastructureDiscoveryService(config=config, logger=logger, api_client=client)


def _provider_config(
    name: str = "my-k8s",
    namespace: str | None = None,
    context: str | None = None,
    in_cluster: bool | None = None,
    service_account: str | None = None,
) -> dict:
    cfg: dict = {}
    if namespace is not None:
        cfg["namespace"] = namespace
    if context is not None:
        cfg["context"] = context
    if in_cluster is not None:
        cfg["in_cluster"] = in_cluster
    template_defaults: dict = {}
    if service_account is not None:
        template_defaults["service_account"] = service_account
    return {"name": name, "config": cfg, "template_defaults": template_defaults}


def _passing_rbac(namespace: str = "default") -> RBACProbeResult:
    return RBACProbeResult(
        namespace=namespace,
        can_create_pods=True,
        can_watch_pods=True,
        can_delete_pods=True,
    )


def _mock_core(
    *,
    get_api_resources_side_effect: object = None,
    read_namespace_side_effect: object = None,
    read_namespaced_sa_side_effect: object = None,
) -> MagicMock:
    """Build a MagicMock CoreV1Api with configurable side-effects."""
    core = MagicMock()
    if get_api_resources_side_effect is not None:
        core.get_api_resources.side_effect = get_api_resources_side_effect
    if read_namespace_side_effect is not None:
        core.read_namespace.side_effect = read_namespace_side_effect
    if read_namespaced_sa_side_effect is not None:
        core.read_namespaced_service_account.side_effect = read_namespaced_sa_side_effect
    return core


# ---------------------------------------------------------------------------
# All checks pass
# ---------------------------------------------------------------------------


class TestValidateInfrastructureAllPass:
    """When every check succeeds the result is ``{valid: True, issues: []}``."""

    def test_valid_true_and_empty_issues(self) -> None:
        svc = _make_service(namespace="prod", context="my-ctx", in_cluster=False)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch(
                "kubernetes.config.list_kube_config_contexts",
                return_value=([{"name": "my-ctx"}], {"name": "my-ctx"}),
            ),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac("prod")),
        ):
            result = svc.validate_infrastructure(
                _provider_config(
                    name="my-k8s",
                    namespace="prod",
                    context="my-ctx",
                    in_cluster=False,
                    service_account="my-sa",
                )
            )

        assert result["provider"] == "my-k8s"
        assert result["valid"] is True
        assert result["issues"] == []


# ---------------------------------------------------------------------------
# Check 1 — API server unreachable
# ---------------------------------------------------------------------------


class TestApiserverUnreachable:
    """When ``get_api_resources`` raises the result must be invalid."""

    def test_apiserver_unreachable_sets_valid_false(self) -> None:
        svc = _make_service(namespace="default", in_cluster=True)
        core = _mock_core(get_api_resources_side_effect=OSError("connection refused"))

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
        ):
            result = svc.validate_infrastructure(_provider_config())

        assert result["valid"] is False
        assert len(result["issues"]) >= 1
        assert _FAKE_ENDPOINT in result["issues"][0]
        assert "connection refused" in result["issues"][0]

    def test_apiserver_unreachable_issue_format(self) -> None:
        """Issue message must match ``'Apiserver unreachable at <endpoint>: <error>'``."""
        svc = _make_service(in_cluster=True)
        error_msg = "timeout after 5s"
        core = _mock_core(get_api_resources_side_effect=Exception(error_msg))

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
        ):
            result = svc.validate_infrastructure(_provider_config())

        assert result["valid"] is False
        issue = result["issues"][0]
        assert issue.startswith("Apiserver unreachable at")
        assert _FAKE_ENDPOINT in issue
        assert error_msg in issue

    def test_apiserver_unreachable_skips_remaining_checks(self) -> None:
        """When the apiserver is unreachable no other checks are performed."""
        svc = _make_service(namespace="default", context="my-ctx", in_cluster=False)
        core = _mock_core(get_api_resources_side_effect=OSError("unreachable"))

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", side_effect=AssertionError("must not be called")),
        ):
            result = svc.validate_infrastructure(_provider_config())

        # Only the apiserver issue must be present.
        assert result["valid"] is False
        assert len(result["issues"]) == 1


# ---------------------------------------------------------------------------
# Check 2 — Context not found
# ---------------------------------------------------------------------------


class TestContextNotFound:
    """When the configured context is absent from kubeconfig an issue is raised."""

    def test_context_not_in_kubeconfig(self) -> None:
        svc = _make_service(namespace="default", context="missing-ctx", in_cluster=False)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch(
                "kubernetes.config.list_kube_config_contexts",
                return_value=([{"name": "other-ctx"}], {"name": "other-ctx"}),
            ),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac()),
        ):
            result = svc.validate_infrastructure(
                _provider_config(context="missing-ctx", in_cluster=False)
            )

        assert result["valid"] is False
        assert any("missing-ctx" in issue for issue in result["issues"])
        assert any("not found in kubeconfig" in issue for issue in result["issues"])

    def test_context_found_in_kubeconfig_no_issue(self) -> None:
        svc = _make_service(namespace="default", context="prod-ctx", in_cluster=False)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch(
                "kubernetes.config.list_kube_config_contexts",
                return_value=(
                    [{"name": "prod-ctx"}, {"name": "dev-ctx"}],
                    {"name": "prod-ctx"},
                ),
            ),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac()),
        ):
            result = svc.validate_infrastructure(
                _provider_config(context="prod-ctx", in_cluster=False)
            )

        assert not any("not found in kubeconfig" in issue for issue in result["issues"])

    def test_context_check_skipped_when_in_cluster(self) -> None:
        """Context check is irrelevant inside a pod — it must be skipped."""
        svc = _make_service(namespace="default", context="some-ctx", in_cluster=True)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch(
                "kubernetes.config.list_kube_config_contexts",
                side_effect=AssertionError("must not be called in-cluster"),
            ),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac()),
        ):
            result = svc.validate_infrastructure(
                _provider_config(context="some-ctx", in_cluster=True)
            )

        assert not any("kubeconfig" in issue for issue in result["issues"])

    def test_context_check_skipped_when_no_context_configured(self) -> None:
        """When no context is configured the context check is not performed."""
        svc = _make_service(namespace="default", in_cluster=False)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch(
                "kubernetes.config.list_kube_config_contexts",
                side_effect=AssertionError("must not be called when context=None"),
            ),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac()),
        ):
            result = svc.validate_infrastructure(_provider_config(context=None, in_cluster=False))

        assert not any("kubeconfig" in issue for issue in result["issues"])


# ---------------------------------------------------------------------------
# Check 3 — Namespace not found
# ---------------------------------------------------------------------------


class TestNamespaceNotFound:
    """Any exception from ``read_namespace`` must produce a namespace-not-found issue."""

    def test_namespace_not_found(self) -> None:
        svc = _make_service(namespace="missing-ns", in_cluster=True)
        not_found = Exception("HTTP 404")
        not_found.status = 404  # type: ignore[attr-defined]
        core = _mock_core(read_namespace_side_effect=not_found)

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac("missing-ns")),
        ):
            result = svc.validate_infrastructure(
                _provider_config(namespace="missing-ns", in_cluster=True)
            )

        assert result["valid"] is False
        assert any("missing-ns" in issue for issue in result["issues"])
        assert any("not found in cluster" in issue for issue in result["issues"])

    def test_namespace_not_found_issue_format(self) -> None:
        """Issue message must be exactly ``"Namespace '<X>' not found in cluster"``."""
        svc = _make_service(namespace="gone", in_cluster=True)
        core = _mock_core(read_namespace_side_effect=Exception("HTTP 404"))

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac("gone")),
        ):
            result = svc.validate_infrastructure(
                _provider_config(namespace="gone", in_cluster=True)
            )

        assert "Namespace 'gone' not found in cluster" in result["issues"]


# ---------------------------------------------------------------------------
# Check 4 — ServiceAccount not found
# ---------------------------------------------------------------------------


class TestServiceAccountNotFound:
    """A failing ``read_namespaced_service_account`` must produce the correct issue."""

    def test_sa_not_found(self) -> None:
        svc = _make_service(namespace="prod", in_cluster=True)
        not_found = Exception("HTTP 404")
        not_found.status = 404  # type: ignore[attr-defined]
        core = _mock_core(read_namespaced_sa_side_effect=not_found)

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac("prod")),
        ):
            result = svc.validate_infrastructure(
                _provider_config(
                    namespace="prod",
                    in_cluster=True,
                    service_account="missing-sa",
                )
            )

        assert result["valid"] is False
        assert any("missing-sa" in issue for issue in result["issues"])
        assert any("prod" in issue for issue in result["issues"])

    def test_sa_not_found_issue_format(self) -> None:
        """Issue must be exactly ``"ServiceAccount '<X>' not found in namespace '<Y>'"``."""
        svc = _make_service(namespace="mynamespace", in_cluster=True)
        core = _mock_core(read_namespaced_sa_side_effect=Exception("HTTP 404"))

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac("mynamespace")),
        ):
            result = svc.validate_infrastructure(
                _provider_config(
                    namespace="mynamespace",
                    in_cluster=True,
                    service_account="my-sa",
                )
            )

        assert "ServiceAccount 'my-sa' not found in namespace 'mynamespace'" in result["issues"]

    def test_no_sa_configured_skips_check(self) -> None:
        """When no service_account is configured the SA check must be skipped."""
        svc = _make_service(namespace="default", in_cluster=True)
        # If SA check fires it will raise AssertionError via the side_effect.
        core = _mock_core(
            read_namespaced_sa_side_effect=AssertionError(
                "must not be called when no SA configured"
            )
        )

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac()),
        ):
            # provider_config has no service_account key
            result = svc.validate_infrastructure(_provider_config(in_cluster=True))

        assert result["valid"] is True
        assert result["issues"] == []


# ---------------------------------------------------------------------------
# Check 5 — RBAC denied
# ---------------------------------------------------------------------------


class TestRBACDenied:
    """Missing pod RBAC verbs must produce one issue per denied verb."""

    @pytest.mark.parametrize(
        "denied_verb,field",
        [
            ("create", "can_create_pods"),
            ("watch", "can_watch_pods"),
            ("delete", "can_delete_pods"),
        ],
    )
    def test_single_denied_verb(self, denied_verb: str, field: str) -> None:
        svc = _make_service(namespace="rbac-ns", in_cluster=True)
        core = _mock_core()
        rbac_kwargs: dict = {
            "namespace": "rbac-ns",
            "can_create_pods": True,
            "can_watch_pods": True,
            "can_delete_pods": True,
        }
        rbac_kwargs[field] = False

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=RBACProbeResult(**rbac_kwargs)),
        ):
            result = svc.validate_infrastructure(
                _provider_config(namespace="rbac-ns", in_cluster=True)
            )

        assert result["valid"] is False
        assert len(result["issues"]) == 1
        assert f"pods.{denied_verb}" in result["issues"][0]
        assert "rbac-ns" in result["issues"][0]

    def test_all_verbs_denied(self) -> None:
        svc = _make_service(namespace="locked-ns", in_cluster=True)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(
                svc,
                "probe_rbac",
                return_value=RBACProbeResult(
                    namespace="locked-ns",
                    can_create_pods=False,
                    can_watch_pods=False,
                    can_delete_pods=False,
                ),
            ),
        ):
            result = svc.validate_infrastructure(
                _provider_config(namespace="locked-ns", in_cluster=True)
            )

        assert result["valid"] is False
        rbac_issues = [i for i in result["issues"] if "Missing permission" in i]
        assert len(rbac_issues) == 3
        verbs_reported = {i.split("pods.")[1].split(" ")[0] for i in rbac_issues}
        assert verbs_reported == {"create", "watch", "delete"}

    def test_rbac_issue_format(self) -> None:
        """Issue must be ``"Missing permission: pods.<verb> in namespace <X>"``."""
        svc = _make_service(namespace="test-ns", in_cluster=True)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(
                svc,
                "probe_rbac",
                return_value=RBACProbeResult(
                    namespace="test-ns",
                    can_create_pods=False,
                    can_watch_pods=True,
                    can_delete_pods=True,
                ),
            ),
        ):
            result = svc.validate_infrastructure(
                _provider_config(namespace="test-ns", in_cluster=True)
            )

        assert "Missing permission: pods.create in namespace test-ns" in result["issues"]

    def test_rbac_probe_exception_adds_issue(self) -> None:
        """When ``probe_rbac`` raises the exception is logged and an issue is added."""
        svc = _make_service(namespace="default", in_cluster=True)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(
                svc,
                "probe_rbac",
                side_effect=K8sDiscoveryError("SelfSubjectAccessReview blocked"),
            ),
        ):
            result = svc.validate_infrastructure(_provider_config(in_cluster=True))

        assert result["valid"] is False
        assert any("RBAC probe failed" in issue for issue in result["issues"])


# ---------------------------------------------------------------------------
# Return shape contract
# ---------------------------------------------------------------------------


class TestReturnShape:
    """``validate_infrastructure`` must always return ``{provider, valid, issues}``."""

    def test_keys_present_on_success(self) -> None:
        svc = _make_service(in_cluster=True)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac()),
        ):
            result = svc.validate_infrastructure(_provider_config(name="my-provider"))

        assert set(result.keys()) >= {"provider", "valid", "issues"}
        assert result["provider"] == "my-provider"
        assert isinstance(result["valid"], bool)
        assert isinstance(result["issues"], list)

    def test_keys_present_on_failure(self) -> None:
        svc = _make_service()
        core = _mock_core(get_api_resources_side_effect=OSError("boom"))

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value="unknown"),
            patch.object(svc, "_core_v1", return_value=core),
        ):
            result = svc.validate_infrastructure(_provider_config(name="fail-provider"))

        assert set(result.keys()) >= {"provider", "valid", "issues"}
        assert result["provider"] == "fail-provider"
        assert result["valid"] is False

    def test_provider_name_from_provider_config(self) -> None:
        svc = _make_service(in_cluster=True)
        core = _mock_core()

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac()),
        ):
            result = svc.validate_infrastructure(
                {"name": "overridden-name", "config": {}, "template_defaults": {}}
            )

        assert result["provider"] == "overridden-name"

    def test_valid_false_has_non_empty_issues(self) -> None:
        """If ``valid=False`` the ``issues`` list must be non-empty."""
        svc = _make_service(in_cluster=True)
        core = _mock_core(read_namespace_side_effect=Exception("HTTP 404"))

        with (
            patch.object(svc, "discover_cluster_endpoint", return_value=_FAKE_ENDPOINT),
            patch.object(svc, "_core_v1", return_value=core),
            patch.object(svc, "probe_rbac", return_value=_passing_rbac()),
        ):
            result = svc.validate_infrastructure(_provider_config(in_cluster=True))

        assert result["valid"] is False
        assert len(result["issues"]) > 0
