"""Integration smoke tests for K8s infrastructure discovery.

Exercises :meth:`K8sInfrastructureDiscoveryService.discover_infrastructure`,
:meth:`discover_infrastructure_interactive`, and
:meth:`validate_infrastructure` end-to-end using a mocked kubernetes client
and a scripted :class:`FakeConsoleAdapter`.  No live cluster is required.

The test boundary sits at the kubernetes SDK: every ``CoreV1Api`` and
``AuthorizationV1Api`` call is intercepted via ``api_client`` injection, and
every ``input()`` call in ``init_prompts`` is patched with a scripted
sequence.  The :class:`K8sInfrastructureDiscoveryService` constructor path,
the prompt dispatch, and the RBAC probe logic run against real production
code; only the network calls are replaced.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Optional
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
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeConsoleAdapter:
    """Console adapter that records all calls for assertion in tests."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.messages.append(("info", message))

    def success(self, message: str) -> None:
        self.messages.append(("success", message))

    def error(self, message: str) -> None:
        self.messages.append(("error", message))

    def warning(self, message: str) -> None:
        self.messages.append(("warning", message))

    def command(self, message: str) -> None:
        self.messages.append(("command", message))

    def separator(self, char: str = "-", width: int = 40, color: str = "") -> None:
        self.messages.append(("separator", char))

    def has_any(self, fragment: str) -> bool:
        return any(fragment in msg for _, msg in self.messages)

    def printed(self) -> list[str]:
        return [msg for _, msg in self.messages]


def _make_ns_item(name: str, phase: str = "Active") -> SimpleNamespace:
    """Return a fake V1Namespace SimpleNamespace."""
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            labels={},
            creation_timestamp=None,
        ),
        status=SimpleNamespace(phase=phase),
    )


def _make_sa_item(name: str, namespace: str = "orb-system") -> SimpleNamespace:
    """Return a fake V1ServiceAccount SimpleNamespace."""
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, annotations={}),
        secrets=[],
    )


def _make_secret_item(name: str) -> SimpleNamespace:
    """Return a fake V1Secret SimpleNamespace."""
    return SimpleNamespace(metadata=SimpleNamespace(name=name))


def _make_sar_response(allowed: bool) -> SimpleNamespace:
    """Return a fake V1SelfSubjectAccessReview response SimpleNamespace."""
    return SimpleNamespace(status=SimpleNamespace(allowed=allowed))


def _make_service(
    namespace: str = "orb-system",
    context: Optional[str] = None,
    api_client: object | None = None,
    console: Optional[FakeConsoleAdapter] = None,
) -> K8sInfrastructureDiscoveryService:
    config = K8sProviderConfig(namespace=namespace, context=context)
    logger = MagicMock()
    return K8sInfrastructureDiscoveryService(
        config=config,
        logger=logger,
        api_client=api_client,
        console=console,
    )


def _make_core_v1_mock(
    namespaces: list[str] | None = None,
    service_accounts: list[str] | None = None,
    pull_secrets: list[str] | None = None,
) -> MagicMock:
    """Build a CoreV1Api mock returning realistic namespace/SA/secret lists."""
    core = MagicMock(name="CoreV1Api")
    # list_namespace
    ns_items = [_make_ns_item(n) for n in (namespaces or ["default", "orb-system"])]
    core.list_namespace.return_value = SimpleNamespace(items=ns_items)
    # list_namespaced_service_account
    sa_items = [_make_sa_item(n) for n in (service_accounts or ["default", "orb-runner"])]
    core.list_namespaced_service_account.return_value = SimpleNamespace(items=sa_items)
    # list_namespaced_secret (pull secrets)
    secret_items = [_make_secret_item(n) for n in (pull_secrets or ["ecr-pull-secret"])]
    core.list_namespaced_secret.return_value = SimpleNamespace(items=secret_items)
    # read_namespace (validate path)
    core.read_namespace.return_value = SimpleNamespace(metadata=SimpleNamespace(name="orb-system"))
    # read_namespaced_service_account (validate path)
    core.read_namespaced_service_account.return_value = SimpleNamespace(
        metadata=SimpleNamespace(name="orb-runner")
    )
    # get_api_resources (health / validate path)
    core.get_api_resources.return_value = SimpleNamespace(resources=[object(), object()])
    return core


def _make_auth_v1_mock(all_allowed: bool = True) -> MagicMock:
    """Build an AuthorizationV1Api mock returning the given RBAC result."""
    auth = MagicMock(name="AuthorizationV1Api")
    auth.create_self_subject_access_review.return_value = _make_sar_response(all_allowed)
    return auth


# ---------------------------------------------------------------------------
# discover_infrastructure — non-interactive
# ---------------------------------------------------------------------------


class TestDiscoverInfrastructureSmokeTest:
    """Exercises the full non-interactive discovery composition."""

    def _build_service(self, console: Optional[FakeConsoleAdapter] = None):
        api_client = MagicMock()
        core = _make_core_v1_mock()
        auth = _make_auth_v1_mock(all_allowed=True)
        svc = _make_service(namespace="orb-system", api_client=api_client, console=console)
        # Patch the helper factories to return our pre-built mocks.
        svc._core_v1 = lambda: core  # type: ignore[method-assign]
        svc._auth_v1 = lambda: auth  # type: ignore[method-assign]
        return svc, core, auth

    def test_returns_all_required_keys(self) -> None:
        """discover_infrastructure returns a dict with every required key."""
        svc, _, _ = self._build_service()

        with (
            patch(
                "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
                return_value=False,
            ),
            patch(
                "kubernetes.config.list_kube_config_contexts",
                return_value=(
                    [{"name": "prod", "context": {"cluster": "c", "user": "u", "namespace": None}}],
                    {"name": "prod"},
                ),
            ),
            patch(
                "kubernetes.config.new_client_from_config",
                return_value=SimpleNamespace(
                    configuration=SimpleNamespace(host="https://1.2.3.4:6443")
                ),
            ),
        ):
            result = svc.discover_infrastructure({"name": "my-k8s"})

        required_keys = {
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
        assert required_keys.issubset(set(result.keys())), (
            f"Missing keys: {required_keys - set(result.keys())}"
        )

    def test_provider_name_in_result(self) -> None:
        svc, _, _ = self._build_service()
        with (
            patch(
                "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
                return_value=False,
            ),
            patch("kubernetes.config.list_kube_config_contexts", return_value=([], None)),
            patch(
                "kubernetes.config.new_client_from_config",
                return_value=SimpleNamespace(configuration=SimpleNamespace(host="unknown")),
            ),
        ):
            result = svc.discover_infrastructure({"name": "integration-provider"})
        assert result["provider"] == "integration-provider"

    def test_in_cluster_false_out_of_cluster(self) -> None:
        svc, _, _ = self._build_service()
        with (
            patch(
                "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
                return_value=False,
            ),
            patch("kubernetes.config.list_kube_config_contexts", return_value=([], None)),
            patch(
                "kubernetes.config.new_client_from_config",
                return_value=SimpleNamespace(
                    configuration=SimpleNamespace(host="https://k8s.example")
                ),
            ),
        ):
            result = svc.discover_infrastructure({"name": "x"})
        assert result["in_cluster"] is False

    def test_namespaces_list_is_populated(self) -> None:
        svc, _, _ = self._build_service()
        with (
            patch(
                "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
                return_value=False,
            ),
            patch("kubernetes.config.list_kube_config_contexts", return_value=([], None)),
            patch(
                "kubernetes.config.new_client_from_config",
                return_value=SimpleNamespace(configuration=SimpleNamespace(host="unknown")),
            ),
        ):
            result = svc.discover_infrastructure({"name": "x"})
        assert "default" in result["namespaces"] or "orb-system" in result["namespaces"]

    def test_rbac_probe_all_granted(self) -> None:
        svc, _, _ = self._build_service()
        with (
            patch(
                "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
                return_value=False,
            ),
            patch("kubernetes.config.list_kube_config_contexts", return_value=([], None)),
            patch(
                "kubernetes.config.new_client_from_config",
                return_value=SimpleNamespace(configuration=SimpleNamespace(host="unknown")),
            ),
        ):
            result = svc.discover_infrastructure({"name": "x"})
        probe = result["rbac_probe"]
        assert probe["create_pods"] is True
        assert probe["watch_pods"] is True
        assert probe["delete_pods"] is True

    def test_image_pull_secrets_returned(self) -> None:
        svc, _, _ = self._build_service()
        with (
            patch(
                "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
                return_value=False,
            ),
            patch("kubernetes.config.list_kube_config_contexts", return_value=([], None)),
            patch(
                "kubernetes.config.new_client_from_config",
                return_value=SimpleNamespace(configuration=SimpleNamespace(host="unknown")),
            ),
        ):
            result = svc.discover_infrastructure({"name": "x"})
        assert "ecr-pull-secret" in result["image_pull_secrets"]

    def test_service_accounts_returned(self) -> None:
        svc, _, _ = self._build_service()
        with (
            patch(
                "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
                return_value=False,
            ),
            patch("kubernetes.config.list_kube_config_contexts", return_value=([], None)),
            patch(
                "kubernetes.config.new_client_from_config",
                return_value=SimpleNamespace(configuration=SimpleNamespace(host="unknown")),
            ),
        ):
            result = svc.discover_infrastructure({"name": "x"})
        assert "orb-runner" in result["service_accounts"]


# ---------------------------------------------------------------------------
# discover_infrastructure_interactive
# ---------------------------------------------------------------------------


class TestDiscoverInteractiveSmokeTest:
    """Exercises the interactive prompt loop with a scripted FakeConsoleAdapter."""

    def _build_svc_with_leaf_mocks(self, console: FakeConsoleAdapter):
        """Build a service and mock all leaf methods directly."""
        api_client = MagicMock()
        config = K8sProviderConfig(namespace="orb-system")
        logger = MagicMock()
        svc = K8sInfrastructureDiscoveryService(
            config=config,
            logger=logger,
            api_client=api_client,
            console=console,
        )
        # Mock leaf methods — tests the interactive composition, not the leaves.
        svc.detect_in_cluster = MagicMock(return_value=False)  # type: ignore[method-assign]
        svc.discover_contexts = MagicMock(  # type: ignore[method-assign]
            return_value=(
                [
                    KubeContextInfo(
                        name="prod", cluster="c", user="u", namespace=None, is_current=True
                    )
                ],
                KubeContextInfo(
                    name="prod", cluster="c", user="u", namespace=None, is_current=True
                ),
            )
        )
        svc.discover_cluster_endpoint = MagicMock(return_value="https://1.2.3.4:6443")  # type: ignore[method-assign]
        svc.discover_namespaces = MagicMock(  # type: ignore[method-assign]
            return_value=[
                NamespaceInfo(name="orb-system", status="Active", age_days=30),
                NamespaceInfo(name="default", status="Active", age_days=365),
            ]
        )
        svc.discover_service_accounts = MagicMock(  # type: ignore[method-assign]
            return_value=[
                ServiceAccountInfo(name="default", namespace="orb-system", secrets_count=0),
                ServiceAccountInfo(name="orb-runner", namespace="orb-system", secrets_count=1),
            ]
        )
        svc.discover_image_pull_secrets = MagicMock(return_value=["ecr-pull-secret"])  # type: ignore[method-assign]
        svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
            return_value=RBACProbeResult(
                namespace="orb-system",
                can_create_pods=True,
                can_watch_pods=True,
                can_delete_pods=True,
            )
        )
        return svc

    def test_interactive_happy_path_returns_expected_shape(self) -> None:
        """Interactive discovery returns the lean operator-chosen shape."""
        console = FakeConsoleAdapter()
        svc = self._build_svc_with_leaf_mocks(console)

        # Script: confirm out-of-cluster=n (stay out), pick namespace 1, skip SA, skip pull-secret.
        # Context is resolved from config/kubeconfig without prompting.
        # RBAC all granted — no additional prompt.
        scripted_inputs = iter(["n", "1", "", ""])
        with patch("builtins.input", side_effect=lambda _prompt="": next(scripted_inputs)):
            result = svc.discover_infrastructure_interactive({"name": "integration-test"})

        # New lean shape: only operator-chosen leaves are returned.
        assert "in_cluster" in result
        assert "namespace" in result
        assert result["in_cluster"] is False
        # context is present when out-of-cluster and a context was resolved
        assert "context" in result

    def test_interactive_picks_defaults(self) -> None:
        """Pressing Enter for every prompt selects the defaults."""
        console = FakeConsoleAdapter()
        svc = self._build_svc_with_leaf_mocks(console)

        # Empty answers: accept in-cluster detection, pick default namespace,
        # skip SA, skip pull-secret.  RBAC all granted — no prompt.
        # Context is resolved without prompting.
        scripted_inputs = iter(["", "", "", ""])
        with patch("builtins.input", side_effect=lambda _prompt="": next(scripted_inputs)):
            result = svc.discover_infrastructure_interactive({"name": "defaults-test"})

        # New shape: only operator-chosen leaves; no "provider" or "rbac_probe" in result.
        assert "in_cluster" in result
        assert "namespace" in result

    def test_interactive_rbac_failure_continue(self) -> None:
        """Operator chooses to continue despite RBAC failure."""
        console = FakeConsoleAdapter()
        svc = self._build_svc_with_leaf_mocks(console)
        # Override RBAC to deny create
        svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
            return_value=RBACProbeResult(
                namespace="orb-system",
                can_create_pods=False,
                can_watch_pods=True,
                can_delete_pods=True,
            )
        )

        # Script: pick ns 1, skip SA, skip pull-secret, continue=y.
        # confirm_in_cluster removed; context resolved without prompting; RBAC denied → extra prompt "y".
        scripted_inputs = iter(["1", "", "", "y"])
        with patch("builtins.input", side_effect=lambda _prompt="": next(scripted_inputs)):
            result = svc.discover_infrastructure_interactive({"name": "rbac-fail-continue"})

        # RBAC probe is not in the return dict; operator was warned and chose to continue.
        assert "in_cluster" in result
        assert "namespace" in result
        assert console.has_any("Missing required permissions")

    def test_interactive_rbac_failure_abort(self) -> None:
        """Operator aborts when RBAC probe fails."""
        console = FakeConsoleAdapter()
        svc = self._build_svc_with_leaf_mocks(console)
        svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
            return_value=RBACProbeResult(
                namespace="orb-system",
                can_create_pods=False,
                can_watch_pods=False,
                can_delete_pods=False,
            )
        )

        # Script: accept defaults then abort on RBAC warning (n)
        scripted_inputs = iter(["", "1", "1", "", "", "n"])
        with patch("builtins.input", side_effect=lambda _prompt="": next(scripted_inputs)):
            with pytest.raises(K8sDiscoveryError, match="aborted"):
                svc.discover_infrastructure_interactive({"name": "rbac-abort"})

    def test_interactive_403_namespace_fallback_skips_prompt(self) -> None:
        """When discover_namespaces returns a 403-fallback single item, the namespace
        prompt is skipped and the auto-selected namespace is used."""
        console = FakeConsoleAdapter()
        svc = self._build_svc_with_leaf_mocks(console)
        # Simulate 403 fallback: one namespace matching the SA-bound file.
        svc.discover_namespaces = MagicMock(  # type: ignore[method-assign]
            return_value=[NamespaceInfo(name="orb-system", status="Active", age_days=0)]
        )

        # Patch the SA-bound namespace file to match.
        _sa_ns_path = (
            "orb.providers.k8s.services.infrastructure_discovery_service._SA_NAMESPACE_FILE"
        )
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "orb-system"

        # Script: accept in-cluster detection (namespace prompt skipped via auto-select),
        # skip SA, skip pull-secret.  Context resolved without prompting; RBAC all granted.
        scripted_inputs = iter(["", "", ""])
        with (
            patch(_sa_ns_path, mock_path),
            patch("builtins.input", side_effect=lambda _prompt="": next(scripted_inputs)),
        ):
            result = svc.discover_infrastructure_interactive({"name": "403-fallback-test"})

        # New shape: namespace key (renamed from default_namespace).
        assert result["namespace"] == "orb-system"
        # The auto-select notice should have appeared
        assert console.has_any("SA-bound namespace")


# ---------------------------------------------------------------------------
# validate_infrastructure
# ---------------------------------------------------------------------------


class TestValidateInfrastructureSmokeTest:
    """Exercises the five-check validation flow with mocked API client."""

    def _build_service_with_mocks(
        self,
        api_reachable: bool = True,
        namespace_exists: bool = True,
        sa_exists: bool = True,
        rbac_all_granted: bool = True,
    ) -> K8sInfrastructureDiscoveryService:
        api_client = MagicMock()
        core = _make_core_v1_mock(namespaces=["orb-system"], service_accounts=["orb-runner"])
        auth = _make_auth_v1_mock(all_allowed=rbac_all_granted)

        if not api_reachable:
            core.get_api_resources.side_effect = ConnectionError("unreachable")
        if not namespace_exists:
            from kubernetes.client.exceptions import ApiException

            exc = ApiException(status=404)
            core.read_namespace.side_effect = exc

        svc = _make_service(
            namespace="orb-system",
            api_client=api_client,
        )
        svc._core_v1 = lambda: core  # type: ignore[method-assign]
        svc._auth_v1 = lambda: auth  # type: ignore[method-assign]
        svc.discover_cluster_endpoint = MagicMock(return_value="https://1.2.3.4:6443")  # type: ignore[method-assign]

        with patch("kubernetes.config.list_kube_config_contexts", return_value=([], None)):
            pass  # pre-warm patching context for thread safety

        return svc

    def test_all_checks_pass_returns_valid(self) -> None:
        svc = self._build_service_with_mocks()
        with (
            patch("kubernetes.config.list_kube_config_contexts", return_value=([], None)),
            patch(
                "orb.providers.k8s.services.infrastructure_discovery_service.is_in_cluster",
                return_value=True,  # skip context check
            ),
        ):
            result = svc.validate_infrastructure(
                {"name": "my-k8s", "config": {"namespace": "orb-system"}}
            )
        assert result["valid"] is True
        assert result["issues"] == []
        assert result["provider"] == "my-k8s"

    def test_api_unreachable_returns_issue(self) -> None:
        api_client = MagicMock()
        config = K8sProviderConfig(namespace="orb-system", in_cluster=True)
        logger = MagicMock()
        svc = K8sInfrastructureDiscoveryService(config=config, logger=logger, api_client=api_client)
        core = MagicMock()
        core.get_api_resources.side_effect = ConnectionError("refused")
        core.read_namespace.return_value = SimpleNamespace(
            metadata=SimpleNamespace(name="orb-system")
        )
        svc._core_v1 = lambda: core  # type: ignore[method-assign]
        svc.discover_cluster_endpoint = MagicMock(return_value="https://down.example:6443")  # type: ignore[method-assign]

        result = svc.validate_infrastructure(
            {"name": "test", "config": {"namespace": "orb-system"}}
        )
        assert result["valid"] is False
        assert any("unreachable" in issue.lower() for issue in result["issues"])

    def test_rbac_failure_reported_as_issue(self) -> None:
        api_client = MagicMock()
        config = K8sProviderConfig(namespace="orb-system", in_cluster=True)
        logger = MagicMock()
        svc = K8sInfrastructureDiscoveryService(config=config, logger=logger, api_client=api_client)
        core = _make_core_v1_mock()
        svc._core_v1 = lambda: core  # type: ignore[method-assign]
        svc.discover_cluster_endpoint = MagicMock(return_value="https://1.2.3.4:6443")  # type: ignore[method-assign]
        # probe_rbac is tested thoroughly in unit tests; mock it here to inject
        # a denial result without patching kubernetes internals.
        svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
            return_value=RBACProbeResult(
                namespace="orb-system",
                can_create_pods=False,
                can_watch_pods=False,
                can_delete_pods=False,
            )
        )

        result = svc.validate_infrastructure(
            {"name": "test", "config": {"namespace": "orb-system"}}
        )
        assert result["valid"] is False
        assert any("pods." in issue for issue in result["issues"])

    def test_service_account_check_when_configured(self) -> None:
        """validate_infrastructure checks the SA when template_defaults.service_account is set."""
        api_client = MagicMock()
        config = K8sProviderConfig(namespace="orb-system", in_cluster=True)
        logger = MagicMock()
        svc = K8sInfrastructureDiscoveryService(config=config, logger=logger, api_client=api_client)
        core = _make_core_v1_mock()
        auth = _make_auth_v1_mock(all_allowed=True)
        svc._core_v1 = lambda: core  # type: ignore[method-assign]
        svc._auth_v1 = lambda: auth  # type: ignore[method-assign]
        svc.discover_cluster_endpoint = MagicMock(return_value="https://1.2.3.4:6443")  # type: ignore[method-assign]

        result = svc.validate_infrastructure(
            {
                "name": "test",
                "config": {"namespace": "orb-system"},
                "template_defaults": {"service_account": "orb-runner"},
            }
        )
        assert result["valid"] is True
        core.read_namespaced_service_account.assert_called_once_with(
            name="orb-runner", namespace="orb-system"
        )
