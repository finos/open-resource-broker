"""Unit tests for the K8s discovery service contract and strategy delegation.

Covers:
* K8sInfrastructureDiscoveryService instantiates without error
* All 7 leaf methods satisfy their interface contracts (with mocked kubernetes SDK)
* K8sProviderStrategy.discover_infrastructure returns a dict shaped per spec
* K8sProviderStrategy.validate_infrastructure returns {provider, valid, issues}
* K8sProviderStrategy._get_discovery_service lazily constructs the service
* Delegation path: strategy methods call the service methods
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.services.discovery_models import RBACProbeResult
from orb.providers.k8s.services.infrastructure_discovery_service import (
    K8sInfrastructureDiscoveryService,
)
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(namespace: str = "default") -> K8sInfrastructureDiscoveryService:
    config = K8sProviderConfig(namespace=namespace)
    logger = MagicMock()
    return K8sInfrastructureDiscoveryService(config=config, logger=logger)


def _make_strategy() -> K8sProviderStrategy:
    """Build a strategy with a mocked K8sClient (no live cluster)."""
    fake_core_v1 = MagicMock()
    fake_core_v1.get_api_resources.return_value = SimpleNamespace(group_version="v1", resources=[])
    fake_client = MagicMock()
    fake_client.core_v1 = fake_core_v1

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        kubernetes_client=fake_client,
    )
    assert strategy.initialize() is True
    return strategy


# ---------------------------------------------------------------------------
# K8sInfrastructureDiscoveryService — instantiation
# ---------------------------------------------------------------------------


class TestDiscoveryServiceInstantiation:
    def test_instantiates_without_error(self) -> None:
        svc = _make_service()
        assert svc is not None

    def test_accepts_injected_api_client(self) -> None:
        config = K8sProviderConfig()
        logger = MagicMock()
        fake_api_client = MagicMock()
        svc = K8sInfrastructureDiscoveryService(
            config=config, logger=logger, api_client=fake_api_client
        )
        assert svc._api_client is fake_api_client  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Leaf method interface contracts (mocked kubernetes SDK)
# ---------------------------------------------------------------------------


class TestLeafMethodContracts:
    """Verify interface contracts for all 7 leaf methods with mocked SDK calls."""

    def test_detect_in_cluster_returns_bool(self) -> None:
        svc = _make_service()
        with patch(
            "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
            return_value=False,
        ):
            result = svc.detect_in_cluster()
        assert result is False

    def test_discover_contexts_returns_tuple_of_list_and_optional(self) -> None:
        svc = _make_service()
        with patch(
            "kubernetes.config.list_kube_config_contexts",
            return_value=([], None),
        ):
            result = svc.discover_contexts()
        assert isinstance(result, tuple)
        assert len(result) == 2
        all_ctxs, current = result
        assert isinstance(all_ctxs, list)
        assert current is None

    def test_discover_cluster_endpoint_returns_string(self) -> None:
        svc = _make_service()
        with patch(
            "kubernetes.config.new_client_from_config",
            side_effect=Exception("no config"),
        ):
            result = svc.discover_cluster_endpoint()
        assert isinstance(result, str)
        assert result == "unknown"

    def test_discover_cluster_endpoint_with_context_returns_string(self) -> None:
        svc = _make_service()
        with patch(
            "kubernetes.config.new_client_from_config",
            side_effect=Exception("no config"),
        ):
            result = svc.discover_cluster_endpoint(context="prod")
        assert isinstance(result, str)

    def test_discover_namespaces_returns_list(self) -> None:
        fake_api = MagicMock()
        fake_api.list_namespace.return_value = SimpleNamespace(items=[])
        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_namespaces()
        assert isinstance(result, list)
        assert result == []

    def test_discover_service_accounts_returns_list(self) -> None:
        fake_api = MagicMock()
        fake_api.list_namespaced_service_account.return_value = SimpleNamespace(items=[])
        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_service_accounts(namespace="default")
        assert isinstance(result, list)
        assert result == []

    def test_discover_image_pull_secrets_returns_list(self) -> None:
        fake_api = MagicMock()
        fake_api.list_namespaced_secret.return_value = SimpleNamespace(items=[])
        svc = _make_service()
        with patch.object(svc, "_core_v1", return_value=fake_api):
            result = svc.discover_image_pull_secrets(namespace="default")
        assert isinstance(result, list)
        assert result == []

    def test_probe_rbac_returns_rbac_probe_result(self) -> None:
        fake_auth = MagicMock()
        fake_status = SimpleNamespace(allowed=False, reason="")
        fake_auth.create_self_subject_access_review.return_value = SimpleNamespace(
            status=fake_status
        )
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
                            result = svc.probe_rbac(namespace="default")
        assert isinstance(result, RBACProbeResult)
        assert result.can_create_pods is False
        assert result.can_watch_pods is False
        assert result.can_delete_pods is False
        assert result.all_granted is False


# ---------------------------------------------------------------------------
# discover_infrastructure shape
# ---------------------------------------------------------------------------


def _make_fully_mocked_service(namespace: str = "default") -> K8sInfrastructureDiscoveryService:
    """Build a service with all leaf methods mocked (no kubernetes SDK calls)."""
    svc = _make_service(namespace=namespace)
    svc.detect_in_cluster = MagicMock(return_value=False)  # type: ignore[method-assign]
    svc.discover_contexts = MagicMock(return_value=([], None))  # type: ignore[method-assign]
    svc.discover_cluster_endpoint = MagicMock(return_value="unknown")  # type: ignore[method-assign]
    svc.discover_namespaces = MagicMock(return_value=[])  # type: ignore[method-assign]
    svc.discover_service_accounts = MagicMock(return_value=[])  # type: ignore[method-assign]
    svc.discover_image_pull_secrets = MagicMock(return_value=[])  # type: ignore[method-assign]
    svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
        return_value=RBACProbeResult(
            namespace=namespace,
            can_create_pods=False,
            can_watch_pods=False,
            can_delete_pods=False,
        )
    )
    return svc


class TestDiscoverInfrastructure:
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

    def test_returns_dict_with_all_spec_keys(self) -> None:
        svc = _make_fully_mocked_service()
        result = svc.discover_infrastructure({"name": "my-k8s", "type": "k8s"})
        assert isinstance(result, dict)
        assert self._REQUIRED_KEYS.issubset(result.keys())

    def test_provider_field_comes_from_config(self) -> None:
        svc = _make_fully_mocked_service()
        result = svc.discover_infrastructure({"name": "my-k8s", "type": "k8s"})
        assert result["provider"] == "my-k8s"

    def test_rbac_probe_has_three_verb_keys(self) -> None:
        svc = _make_fully_mocked_service()
        result = svc.discover_infrastructure({})
        rbac = result["rbac_probe"]
        assert "create_pods" in rbac
        assert "watch_pods" in rbac
        assert "delete_pods" in rbac

    def test_default_namespace_falls_back_to_configured(self) -> None:
        svc = _make_fully_mocked_service(namespace="orb-system")
        result = svc.discover_infrastructure({})
        assert result["default_namespace"] == "orb-system"

    def test_contexts_and_service_accounts_are_lists(self) -> None:
        svc = _make_fully_mocked_service()
        result = svc.discover_infrastructure({})
        assert isinstance(result["contexts"], list)
        assert isinstance(result["service_accounts"], list)
        assert isinstance(result["image_pull_secrets"], list)
        assert isinstance(result["namespaces"], list)


# ---------------------------------------------------------------------------
# validate_infrastructure shape
# ---------------------------------------------------------------------------


class TestValidateInfrastructure:
    def test_returns_valid_true_with_empty_issues(self) -> None:
        svc = _make_service()
        result = svc.validate_infrastructure({"name": "my-k8s", "type": "k8s"})
        assert result["valid"] is True
        assert result["issues"] == []
        assert result["provider"] == "my-k8s"

    def test_issues_is_a_list(self) -> None:
        svc = _make_service()
        result = svc.validate_infrastructure({})
        assert isinstance(result["issues"], list)


# ---------------------------------------------------------------------------
# K8sProviderStrategy — discovery delegation
# ---------------------------------------------------------------------------


class TestStrategyDiscoveryDelegation:
    def test_lazy_getter_returns_discovery_service_instance(self) -> None:
        strategy = _make_strategy()
        svc = strategy._get_discovery_service()  # type: ignore[attr-defined]
        assert isinstance(svc, K8sInfrastructureDiscoveryService)

    def test_lazy_getter_caches_same_instance(self) -> None:
        strategy = _make_strategy()
        svc1 = strategy._get_discovery_service()  # type: ignore[attr-defined]
        svc2 = strategy._get_discovery_service()  # type: ignore[attr-defined]
        assert svc1 is svc2

    def test_discover_infrastructure_returns_dict(self) -> None:
        strategy = _make_strategy()
        # Pre-populate with a fully mocked discovery service to avoid SDK calls.
        fake_service = MagicMock()
        fake_service.discover_infrastructure.return_value = {
            "in_cluster": False,
            "contexts": [],
            "current_context": None,
            "cluster_endpoint": "unknown",
            "namespaces": [],
            "default_namespace": "default",
            "service_accounts": [],
            "image_pull_secrets": [],
            "rbac_probe": {"create_pods": False, "watch_pods": False, "delete_pods": False},
            "provider": "test",
        }
        strategy._discovery_service = fake_service  # type: ignore[attr-defined]
        result = strategy.discover_infrastructure({"type": "k8s", "name": "test"})
        assert isinstance(result, dict)
        assert "provider" in result
        assert "valid" not in result  # should NOT look like validate result

    def test_discover_infrastructure_interactive_returns_dict(self) -> None:
        strategy = _make_strategy()
        fake_service = MagicMock()
        fake_service.discover_infrastructure_interactive.return_value = {
            "in_cluster": False,
            "contexts": [],
            "current_context": None,
            "cluster_endpoint": "unknown",
            "namespaces": [],
            "default_namespace": "default",
            "service_accounts": [],
            "image_pull_secrets": [],
            "rbac_probe": {"create_pods": False, "watch_pods": False, "delete_pods": False},
            "provider": "test",
        }
        strategy._discovery_service = fake_service  # type: ignore[attr-defined]
        result = strategy.discover_infrastructure_interactive({"type": "k8s", "name": "test"})
        assert isinstance(result, dict)

    def test_validate_infrastructure_returns_valid_true(self) -> None:
        strategy = _make_strategy()
        result = strategy.validate_infrastructure({"type": "k8s", "name": "test"})
        assert isinstance(result, dict)
        assert result["valid"] is True
        assert result["issues"] == []

    def test_strategy_delegates_to_service_not_reimplements(self) -> None:
        """Strategy must delegate; verify by patching the service method."""
        strategy = _make_strategy()
        fake_service = MagicMock()
        fake_service.discover_infrastructure.return_value = {"provider": "mocked"}
        fake_service.discover_infrastructure_interactive.return_value = {"provider": "mocked-i"}
        fake_service.validate_infrastructure.return_value = {
            "provider": "mocked-v",
            "valid": True,
            "issues": [],
        }
        strategy._discovery_service = fake_service  # type: ignore[attr-defined]

        assert strategy.discover_infrastructure({}) == {"provider": "mocked"}
        assert strategy.discover_infrastructure_interactive({}) == {"provider": "mocked-i"}
        assert strategy.validate_infrastructure({}) == {
            "provider": "mocked-v",
            "valid": True,
            "issues": [],
        }
        fake_service.discover_infrastructure.assert_called_once()
        fake_service.discover_infrastructure_interactive.assert_called_once()
        fake_service.validate_infrastructure.assert_called_once()
