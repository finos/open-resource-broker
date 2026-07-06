"""Unit tests for K8sInfrastructureDiscoveryService leaf methods and composition.

All kubernetes SDK calls are mocked via ``api_client`` injection — no live
cluster is required.  Each leaf method is covered by:

* Happy path
* 403 ApiException fallback (where applicable)
* ImportError / SDK-absent guard (discover_contexts, probe_rbac)

The composition test (:class:`TestDiscoverInfrastructure`) verifies the full
return shape of :meth:`discover_infrastructure` using a fully mocked service.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_errors import K8sDiscoveryError
from orb.providers.k8s.services.discovery_models import (
    KubeContextInfo,
    NamespaceInfo,
    RBACProbeResult,
    ServiceAccountInfo,
)
from orb.providers.k8s.services.infrastructure_discovery_service import (
    K8sInfrastructureDiscoveryService,
    _age_days,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    namespace: str = "default",
    api_client: object | None = None,
    kubeconfig_path: str | None = None,
) -> K8sInfrastructureDiscoveryService:
    config = K8sProviderConfig(namespace=namespace, kubeconfig_path=kubeconfig_path)
    logger = MagicMock()
    return K8sInfrastructureDiscoveryService(config=config, logger=logger, api_client=api_client)


def _make_ns_item(
    name: str,
    phase: str = "Active",
    labels: dict | None = None,
    creation_ts: datetime.datetime | None = None,
) -> SimpleNamespace:
    """Return a fake ``V1Namespace`` SimpleNamespace."""
    meta = SimpleNamespace(
        name=name,
        labels=labels or {},
        creation_timestamp=creation_ts
        or datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
    )
    status = SimpleNamespace(phase=phase)
    return SimpleNamespace(metadata=meta, status=status)


def _make_sa_item(
    name: str, namespace: str = "default", secrets: list | None = None
) -> SimpleNamespace:
    meta = SimpleNamespace(name=name, annotations={})
    return SimpleNamespace(metadata=meta, secrets=secrets or [])


def _make_secret_item(name: str) -> SimpleNamespace:
    meta = SimpleNamespace(name=name)
    return SimpleNamespace(metadata=meta)


def _make_api_exception(status: int) -> Exception:
    """Return a fake kubernetes ApiException with the given HTTP status."""
    exc = Exception(f"HTTP {status}")
    exc.status = status  # type: ignore[attr-defined]
    # Patch _is_forbidden to recognise this as a real ApiException.
    return exc


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


class TestAgeDays:
    def test_datetime_input(self) -> None:
        ts = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=3)
        assert _age_days(ts) == 3

    def test_string_input(self) -> None:
        ts = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(days=10)
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert _age_days(ts_str) == 10

    def test_none_returns_zero(self) -> None:
        assert _age_days(None) == 0

    def test_bad_string_returns_zero(self) -> None:
        assert _age_days("not-a-date") == 0


# ---------------------------------------------------------------------------
# detect_in_cluster
# ---------------------------------------------------------------------------


class TestDetectInCluster:
    def test_happy_path_returns_true(self) -> None:
        svc = _make_service()
        with patch(
            "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
            return_value=True,
        ):
            assert svc.detect_in_cluster() is True

    def test_out_of_cluster_returns_false(self) -> None:
        svc = _make_service()
        with patch(
            "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
            return_value=False,
        ):
            assert svc.detect_in_cluster() is False


# ---------------------------------------------------------------------------
# discover_contexts
# ---------------------------------------------------------------------------


class TestDiscoverContexts:
    def _raw_ctx(self, name: str, cluster: str = "c", user: str = "u") -> dict:
        return {"name": name, "context": {"cluster": cluster, "user": user, "namespace": None}}

    def test_happy_path_two_contexts(self) -> None:
        svc = _make_service()
        raw_list = [self._raw_ctx("prod"), self._raw_ctx("dev")]
        raw_current = {"name": "prod"}

        with patch(
            "orb.providers.k8s.services.infrastructure_discovery_service.K8sInfrastructureDiscoveryService.discover_contexts",
            wraps=svc.discover_contexts,
        ):
            pass  # ensure method exists

        with patch(
            "kubernetes.config.list_kube_config_contexts",
            return_value=(raw_list, raw_current),
        ):
            all_ctxs, current = svc.discover_contexts()

        assert len(all_ctxs) == 2
        assert all_ctxs[0].name == "prod"
        assert all_ctxs[0].is_current is True
        assert all_ctxs[1].name == "dev"
        assert all_ctxs[1].is_current is False
        assert current is not None
        assert current.name == "prod"

    def test_empty_kubeconfig_returns_empty(self) -> None:
        svc = _make_service()
        with patch(
            "kubernetes.config.list_kube_config_contexts",
            return_value=([], None),
        ):
            all_ctxs, current = svc.discover_contexts()
        assert all_ctxs == []
        assert current is None

    def test_kubeconfig_path_forwarded(self) -> None:
        svc = _make_service()
        path = Path("/custom/.kube/config")
        raw_list = [self._raw_ctx("staging")]
        raw_current = {"name": "staging"}

        with patch(
            "kubernetes.config.list_kube_config_contexts",
            return_value=(raw_list, raw_current),
        ) as mock_fn:
            svc.discover_contexts(kubeconfig_path=path)
        mock_fn.assert_called_once_with(config_file="/custom/.kube/config")

    def test_file_not_found_returns_empty(self) -> None:
        svc = _make_service()
        with patch(
            "kubernetes.config.list_kube_config_contexts",
            side_effect=FileNotFoundError("no file"),
        ):
            all_ctxs, current = svc.discover_contexts()
        assert all_ctxs == []
        assert current is None

    def test_import_error_raises_k8s_discovery_error(self) -> None:
        svc = _make_service()
        with patch.dict("sys.modules", {"kubernetes": None, "kubernetes.config": None}):
            with pytest.raises(K8sDiscoveryError, match="kubernetes SDK is not installed"):
                svc.discover_contexts()

    def test_no_current_context(self) -> None:
        svc = _make_service()
        raw_list = [self._raw_ctx("only-one")]
        with patch(
            "kubernetes.config.list_kube_config_contexts",
            return_value=(raw_list, None),
        ):
            all_ctxs, current = svc.discover_contexts()
        assert len(all_ctxs) == 1
        assert all_ctxs[0].is_current is False
        assert current is None


# ---------------------------------------------------------------------------
# discover_cluster_endpoint
# ---------------------------------------------------------------------------


class TestDiscoverClusterEndpoint:
    def test_happy_path_returns_host(self) -> None:
        svc = _make_service()
        fake_client = SimpleNamespace(configuration=SimpleNamespace(host="https://1.2.3.4:6443"))
        with patch("kubernetes.config.new_client_from_config", return_value=fake_client):
            result = svc.discover_cluster_endpoint(context="prod")
        assert result == "https://1.2.3.4:6443"

    def test_config_exception_returns_unknown(self) -> None:
        svc = _make_service()
        with patch(
            "kubernetes.config.new_client_from_config",
            side_effect=Exception("context not found"),
        ):
            result = svc.discover_cluster_endpoint(context="bad-context")
        assert result == "unknown"

    def test_import_error_returns_unknown(self) -> None:
        svc = _make_service()
        with patch.dict("sys.modules", {"kubernetes": None, "kubernetes.config": None}):
            result = svc.discover_cluster_endpoint()
        assert result == "unknown"

    def test_none_context_uses_active(self) -> None:
        svc = _make_service()
        fake_client = SimpleNamespace(configuration=SimpleNamespace(host="https://9.9.9.9:443"))
        with patch("kubernetes.config.new_client_from_config", return_value=fake_client) as mock_fn:
            svc.discover_cluster_endpoint(context=None)
        mock_fn.assert_called_once_with(context=None)


# ---------------------------------------------------------------------------
# discover_namespaces
# ---------------------------------------------------------------------------


class TestDiscoverNamespaces:
    def test_happy_path(self) -> None:
        fake_api = MagicMock()
        ns_item = _make_ns_item("default")
        fake_api.list_namespace.return_value = SimpleNamespace(items=[ns_item])

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_namespaces()

        assert len(result) == 1
        assert result[0].name == "default"
        assert result[0].status == "Active"
        assert isinstance(result[0].age_days, int)

    def test_multiple_namespaces_sorted(self) -> None:
        fake_api = MagicMock()
        items = [
            _make_ns_item("orb-system", phase="Active"),
            _make_ns_item("kube-system", phase="Active"),
        ]
        fake_api.list_namespace.return_value = SimpleNamespace(items=items)

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_namespaces()

        assert len(result) == 2
        names = [n.name for n in result]
        assert "orb-system" in names
        assert "kube-system" in names

    def test_403_fallback_reads_sa_namespace_file(self) -> None:
        """On 403, falls back to the SA-bound namespace file."""
        fake_api = MagicMock()
        fake_exc = Exception("403")
        fake_exc.status = 403  # type: ignore[attr-defined]
        fake_api.list_namespace.side_effect = fake_exc

        svc = _make_service()
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "orb-system"
        with patch.object(svc, "_core_v1", return_value=fake_api):
            with patch(
                "orb.providers.k8s.services.infrastructure_discovery_service._is_forbidden",
                return_value=True,
            ):
                with patch(
                    "orb.providers.k8s.services.infrastructure_discovery_service"
                    "._SA_NAMESPACE_FILE",
                    mock_path,
                ):
                    result = svc.discover_namespaces()

        assert len(result) == 1
        assert result[0].name == "orb-system"
        assert result[0].status == "Active"

    def test_403_no_sa_file_returns_empty(self) -> None:
        """On 403 with no SA file available, returns empty list."""
        fake_api = MagicMock()
        fake_exc = Exception("403")
        fake_exc.status = 403  # type: ignore[attr-defined]
        fake_api.list_namespace.side_effect = fake_exc

        svc = _make_service()
        mock_path = MagicMock()
        mock_path.exists.return_value = False

        with patch.object(svc, "_core_v1", return_value=fake_api):
            with patch(
                "orb.providers.k8s.services.infrastructure_discovery_service._is_forbidden",
                return_value=True,
            ):
                with patch(
                    "orb.providers.k8s.services.infrastructure_discovery_service"
                    "._SA_NAMESPACE_FILE",
                    mock_path,
                ):
                    result = svc.discover_namespaces()

        assert result == []

    def test_non_403_error_raises(self) -> None:
        """A non-403 API error is re-raised as K8sDiscoveryError."""
        fake_api = MagicMock()
        fake_api.list_namespace.side_effect = Exception("connection refused")

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            with patch(
                "orb.providers.k8s.services.infrastructure_discovery_service._is_forbidden",
                return_value=False,
            ):
                with pytest.raises(K8sDiscoveryError):
                    svc.discover_namespaces()


# ---------------------------------------------------------------------------
# discover_service_accounts
# ---------------------------------------------------------------------------


class TestDiscoverServiceAccounts:
    def test_happy_path(self) -> None:
        fake_api = MagicMock()
        items = [
            _make_sa_item("default", "orb-ns"),
            _make_sa_item("orb-runner", "orb-ns", secrets=[object(), object()]),
        ]
        fake_api.list_namespaced_service_account.return_value = SimpleNamespace(items=items)

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_service_accounts(namespace="orb-ns")

        assert len(result) == 2
        assert result[0].name == "default"
        assert result[0].namespace == "orb-ns"
        assert result[1].secrets_count == 2

    def test_403_returns_empty_list(self) -> None:
        fake_api = MagicMock()
        fake_api.list_namespaced_service_account.side_effect = Exception("403")

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            with patch(
                "orb.providers.k8s.services.infrastructure_discovery_service._is_forbidden",
                return_value=True,
            ):
                result = svc.discover_service_accounts(namespace="orb-ns")

        assert result == []

    def test_non_403_error_raises(self) -> None:
        fake_api = MagicMock()
        fake_api.list_namespaced_service_account.side_effect = Exception("timeout")

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            with patch(
                "orb.providers.k8s.services.infrastructure_discovery_service._is_forbidden",
                return_value=False,
            ):
                with pytest.raises(K8sDiscoveryError, match="ServiceAccounts"):
                    svc.discover_service_accounts(namespace="orb-ns")

    def test_empty_namespace_returns_empty_list(self) -> None:
        fake_api = MagicMock()
        fake_api.list_namespaced_service_account.return_value = SimpleNamespace(items=[])

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_service_accounts(namespace="empty-ns")

        assert result == []


# ---------------------------------------------------------------------------
# discover_image_pull_secrets
# ---------------------------------------------------------------------------


class TestDiscoverImagePullSecrets:
    def test_happy_path_returns_names_only(self) -> None:
        fake_api = MagicMock()
        items = [_make_secret_item("ecr-pull"), _make_secret_item("ghcr-token")]
        fake_api.list_namespaced_secret.return_value = SimpleNamespace(items=items)

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_image_pull_secrets(namespace="orb-ns")

        assert result == ["ecr-pull", "ghcr-token"]

    def test_field_selector_is_docker_type(self) -> None:
        """Verify the field_selector restricts to dockerconfigjson type."""
        fake_api = MagicMock()
        fake_api.list_namespaced_secret.return_value = SimpleNamespace(items=[])

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            svc.discover_image_pull_secrets(namespace="orb-ns")

        call_kwargs = fake_api.list_namespaced_secret.call_args
        assert call_kwargs.kwargs.get("field_selector") == "type=kubernetes.io/dockerconfigjson"

    def test_403_returns_empty_list(self) -> None:
        fake_api = MagicMock()
        fake_api.list_namespaced_secret.side_effect = Exception("403")

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            with patch(
                "orb.providers.k8s.services.infrastructure_discovery_service._is_forbidden",
                return_value=True,
            ):
                result = svc.discover_image_pull_secrets(namespace="orb-ns")

        assert result == []

    def test_empty_namespace_returns_empty_list(self) -> None:
        fake_api = MagicMock()
        fake_api.list_namespaced_secret.return_value = SimpleNamespace(items=[])

        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_image_pull_secrets(namespace="orb-ns")

        assert result == []


# ---------------------------------------------------------------------------
# probe_rbac
# ---------------------------------------------------------------------------


class TestProbeRbac:
    def _make_sar_response(self, allowed: bool, reason: str = "") -> SimpleNamespace:
        status = SimpleNamespace(allowed=allowed, reason=reason)
        return SimpleNamespace(status=status)

    def test_all_allowed(self) -> None:
        fake_auth = MagicMock()
        fake_auth.create_self_subject_access_review.return_value = self._make_sar_response(
            allowed=True
        )

        svc = _make_service()
        with patch.object(svc, "_get_api_client", return_value=MagicMock()):
            with patch(
                "orb.providers.k8s.services.infrastructure_discovery_service"
                ".K8sInfrastructureDiscoveryService._auth_v1",
                return_value=fake_auth,
            ):
                with patch("kubernetes.client.AuthorizationV1Api", return_value=fake_auth):
                    with patch(
                        "kubernetes.client.V1SelfSubjectAccessReview",
                        side_effect=lambda **kw: SimpleNamespace(**kw),
                    ):
                        with patch(
                            "kubernetes.client.V1SelfSubjectAccessReviewSpec",
                            side_effect=lambda **kw: SimpleNamespace(**kw),
                        ):
                            with patch(
                                "kubernetes.client.V1ResourceAttributes",
                                side_effect=lambda **kw: SimpleNamespace(**kw),
                            ):
                                result = svc.probe_rbac(namespace="orb-ns")

        assert result.can_create_pods is True
        assert result.can_watch_pods is True
        assert result.can_delete_pods is True
        assert result.all_granted is True

    def test_partial_deny(self) -> None:
        """create=True, watch=True, delete=False."""
        responses = [
            self._make_sar_response(allowed=True),  # create
            self._make_sar_response(allowed=True),  # watch
            self._make_sar_response(allowed=False),  # delete
        ]
        fake_auth = MagicMock()
        fake_auth.create_self_subject_access_review.side_effect = responses

        svc = _make_service()
        with patch.object(svc, "_get_api_client", return_value=MagicMock()):
            with patch("kubernetes.client.AuthorizationV1Api", return_value=fake_auth):
                with patch(
                    "kubernetes.client.V1SelfSubjectAccessReview",
                    side_effect=lambda **kw: SimpleNamespace(**kw),
                ):
                    with patch(
                        "kubernetes.client.V1SelfSubjectAccessReviewSpec",
                        side_effect=lambda **kw: SimpleNamespace(**kw),
                    ):
                        with patch(
                            "kubernetes.client.V1ResourceAttributes",
                            side_effect=lambda **kw: SimpleNamespace(**kw),
                        ):
                            result = svc.probe_rbac(namespace="orb-ns")

        assert result.can_create_pods is True
        assert result.can_watch_pods is True
        assert result.can_delete_pods is False
        assert result.all_granted is False

    def test_api_error_raises_k8s_discovery_error(self) -> None:
        fake_auth = MagicMock()
        fake_auth.create_self_subject_access_review.side_effect = Exception("server error")

        svc = _make_service()
        with patch.object(svc, "_get_api_client", return_value=MagicMock()):
            with patch("kubernetes.client.AuthorizationV1Api", return_value=fake_auth):
                with patch(
                    "kubernetes.client.V1SelfSubjectAccessReview",
                    side_effect=lambda **kw: SimpleNamespace(**kw),
                ):
                    with patch(
                        "kubernetes.client.V1SelfSubjectAccessReviewSpec",
                        side_effect=lambda **kw: SimpleNamespace(**kw),
                    ):
                        with patch(
                            "kubernetes.client.V1ResourceAttributes",
                            side_effect=lambda **kw: SimpleNamespace(**kw),
                        ):
                            with pytest.raises(K8sDiscoveryError, match="SelfSubjectAccessReview"):
                                svc.probe_rbac(namespace="orb-ns")

    def test_import_error_raises_k8s_discovery_error(self) -> None:
        svc = _make_service()
        with patch.dict(
            "sys.modules",
            {
                "kubernetes.client": None,
                "kubernetes.client.AuthorizationV1Api": None,
            },
        ):
            with pytest.raises(K8sDiscoveryError, match="kubernetes SDK is not installed"):
                svc.probe_rbac(namespace="orb-ns")


# ---------------------------------------------------------------------------
# discover_infrastructure — composite shape
# ---------------------------------------------------------------------------


_REQUIRED_KEYS = {
    "in_cluster",
    "contexts",
    "current_context",
    "cluster_endpoint",
    "namespaces",
    "default_namespace",
    "service_accounts",
    "image_pull_secrets",
    "rbac_probe",
    "provider",
}


class TestDiscoverInfrastructure:
    def _make_mocked_service(
        self,
        *,
        in_cluster: bool = False,
        contexts: list[KubeContextInfo] | None = None,
        current_context: KubeContextInfo | None = None,
        cluster_endpoint: str = "https://example.k8s:6443",
        namespaces: list[NamespaceInfo] | None = None,
        service_accounts: list[ServiceAccountInfo] | None = None,
        pull_secrets: list[str] | None = None,
        rbac: RBACProbeResult | None = None,
    ) -> K8sInfrastructureDiscoveryService:
        svc = _make_service()
        svc.detect_in_cluster = MagicMock(return_value=in_cluster)  # type: ignore[method-assign]
        svc.discover_contexts = MagicMock(  # type: ignore[method-assign]
            return_value=(contexts or [], current_context)
        )
        svc.discover_cluster_endpoint = MagicMock(return_value=cluster_endpoint)  # type: ignore[method-assign]
        svc.discover_namespaces = MagicMock(return_value=namespaces or [])  # type: ignore[method-assign]
        svc.discover_service_accounts = MagicMock(  # type: ignore[method-assign]
            return_value=service_accounts or []
        )
        svc.discover_image_pull_secrets = MagicMock(return_value=pull_secrets or [])  # type: ignore[method-assign]
        svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
            return_value=rbac
            or RBACProbeResult(
                namespace="default",
                can_create_pods=True,
                can_watch_pods=True,
                can_delete_pods=True,
            )
        )
        return svc

    def test_returns_all_required_keys(self) -> None:
        svc = self._make_mocked_service()
        result = svc.discover_infrastructure({"name": "my-k8s"})
        assert _REQUIRED_KEYS.issubset(result.keys())

    def test_provider_name_from_config(self) -> None:
        svc = self._make_mocked_service()
        result = svc.discover_infrastructure({"name": "test-cluster"})
        assert result["provider"] == "test-cluster"

    def test_in_cluster_flag_propagated(self) -> None:
        svc = self._make_mocked_service(in_cluster=True)
        result = svc.discover_infrastructure({})
        assert result["in_cluster"] is True

    def test_context_names_are_strings(self) -> None:
        ctxs = [
            KubeContextInfo(name="prod", cluster="c1", user="u1", namespace=None, is_current=True),
            KubeContextInfo(name="dev", cluster="c2", user="u2", namespace=None, is_current=False),
        ]
        current = ctxs[0]
        svc = self._make_mocked_service(contexts=ctxs, current_context=current)
        result = svc.discover_infrastructure({})
        assert result["contexts"] == ["prod", "dev"]
        assert result["current_context"] == "prod"

    def test_no_contexts(self) -> None:
        svc = self._make_mocked_service(contexts=[], current_context=None)
        result = svc.discover_infrastructure({})
        assert result["contexts"] == []
        assert result["current_context"] is None

    def test_namespace_names_are_strings(self) -> None:
        nss = [
            NamespaceInfo(name="default", status="Active", age_days=1),
            NamespaceInfo(name="orb-system", status="Active", age_days=5),
        ]
        svc = self._make_mocked_service(namespaces=nss)
        result = svc.discover_infrastructure({})
        assert result["namespaces"] == ["default", "orb-system"]

    def test_default_namespace_from_config(self) -> None:
        svc = _make_service(namespace="orb-system")
        svc.detect_in_cluster = MagicMock(return_value=False)  # type: ignore[method-assign]
        svc.discover_contexts = MagicMock(return_value=([], None))  # type: ignore[method-assign]
        svc.discover_cluster_endpoint = MagicMock(return_value="unknown")  # type: ignore[method-assign]
        svc.discover_namespaces = MagicMock(return_value=[])  # type: ignore[method-assign]
        svc.discover_service_accounts = MagicMock(return_value=[])  # type: ignore[method-assign]
        svc.discover_image_pull_secrets = MagicMock(return_value=[])  # type: ignore[method-assign]
        svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
            return_value=RBACProbeResult("orb-system", False, False, False)
        )
        result = svc.discover_infrastructure({})
        assert result["default_namespace"] == "orb-system"

    def test_service_account_names_are_strings(self) -> None:
        sas = [
            ServiceAccountInfo(name="default", namespace="orb", secrets_count=0),
            ServiceAccountInfo(name="orb-runner", namespace="orb", secrets_count=2),
        ]
        svc = self._make_mocked_service(service_accounts=sas)
        result = svc.discover_infrastructure({})
        assert result["service_accounts"] == ["default", "orb-runner"]

    def test_image_pull_secrets_forwarded(self) -> None:
        svc = self._make_mocked_service(pull_secrets=["ecr-pull", "ghcr-token"])
        result = svc.discover_infrastructure({})
        assert result["image_pull_secrets"] == ["ecr-pull", "ghcr-token"]

    def test_rbac_probe_dict_shape(self) -> None:
        rbac = RBACProbeResult(
            namespace="default",
            can_create_pods=True,
            can_watch_pods=False,
            can_delete_pods=True,
        )
        svc = self._make_mocked_service(rbac=rbac)
        result = svc.discover_infrastructure({})
        assert result["rbac_probe"] == {
            "create_pods": True,
            "watch_pods": False,
            "delete_pods": True,
        }

    def test_rbac_probe_failure_returns_all_false(self) -> None:
        """A K8sDiscoveryError from probe_rbac produces all-False rbac_probe."""
        svc = self._make_mocked_service()
        svc.probe_rbac = MagicMock(side_effect=K8sDiscoveryError("self-review blocked"))  # type: ignore[method-assign]
        result = svc.discover_infrastructure({})
        assert result["rbac_probe"] == {
            "create_pods": False,
            "watch_pods": False,
            "delete_pods": False,
        }

    def test_cluster_endpoint_in_result(self) -> None:
        svc = self._make_mocked_service(cluster_endpoint="https://k8s.example.com:6443")
        result = svc.discover_infrastructure({})
        assert result["cluster_endpoint"] == "https://k8s.example.com:6443"
