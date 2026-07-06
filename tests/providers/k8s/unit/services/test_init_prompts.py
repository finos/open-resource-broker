"""Unit tests for ``orb.providers.k8s.services.init_prompts``.

All prompt functions are pure I/O (no kubernetes SDK calls).  Tests inject:

* A :class:`FakeConsoleAdapter` that records calls for assertion.
* Patched ``builtins.input`` to provide scripted operator responses.

The ``discover_infrastructure_interactive`` composition is tested via a
:class:`K8sInfrastructureDiscoveryService` whose leaf methods are fully
mocked, so no live cluster is required.
"""

from __future__ import annotations

from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_errors import K8sDiscoveryError, K8sError
from orb.providers.k8s.services.discovery_models import (
    KubeContextInfo,
    NamespaceInfo,
    RBACProbeResult,
    ServiceAccountInfo,
)
from orb.providers.k8s.services.infrastructure_discovery_service import (
    K8sInfrastructureDiscoveryService,
)
from orb.providers.k8s.services.init_prompts import (
    confirm_in_cluster,
    display_rbac_probe,
    pick_context,
    pick_image_pull_secret,
    pick_namespace,
    pick_service_account,
)

# ---------------------------------------------------------------------------
# FakeConsoleAdapter
# ---------------------------------------------------------------------------


class FakeConsoleAdapter:
    """Console adapter that records all calls for assertion in unit tests."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []  # (method, message)

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

    def printed(self) -> list[str]:
        """Return all message strings in order (without method labels)."""
        return [msg for _, msg in self.messages]

    def printed_methods(self) -> list[str]:
        return [method for method, _ in self.messages]

    def has_any(self, fragment: str) -> bool:
        return any(fragment in msg for _, msg in self.messages)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(name: str, is_current: bool = False) -> KubeContextInfo:
    return KubeContextInfo(name=name, cluster="c", user="u", namespace=None, is_current=is_current)


def _ns(name: str, status: str = "Active") -> NamespaceInfo:
    return NamespaceInfo(name=name, status=status, age_days=0)


def _sa(name: str, namespace: str = "default") -> ServiceAccountInfo:
    return ServiceAccountInfo(name=name, namespace=namespace, secrets_count=0)


def _rbac(
    namespace: str = "default",
    create: bool = True,
    watch: bool = True,
    delete: bool = True,
) -> RBACProbeResult:
    return RBACProbeResult(
        namespace=namespace,
        can_create_pods=create,
        can_watch_pods=watch,
        can_delete_pods=delete,
    )


def _make_service(
    namespace: str = "default",
    console: Optional[FakeConsoleAdapter] = None,
) -> K8sInfrastructureDiscoveryService:
    config = K8sProviderConfig(namespace=namespace)
    logger = MagicMock()
    svc = K8sInfrastructureDiscoveryService(
        config=config,
        logger=logger,
        console=console or FakeConsoleAdapter(),
    )
    return svc


def _make_fully_mocked_service(
    console: Optional[FakeConsoleAdapter] = None,
    *,
    in_cluster: bool = False,
    contexts: list[KubeContextInfo] | None = None,
    current_context: KubeContextInfo | None = None,
    cluster_endpoint: str = "https://k8s.test:6443",
    namespaces: list[NamespaceInfo] | None = None,
    service_accounts: list[ServiceAccountInfo] | None = None,
    pull_secrets: list[str] | None = None,
    rbac: RBACProbeResult | None = None,
) -> K8sInfrastructureDiscoveryService:
    svc = _make_service(console=console)
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
        return_value=rbac or _rbac()
    )
    return svc


# ===========================================================================
# confirm_in_cluster
# ===========================================================================


class TestConfirmInCluster:
    def test_default_accepts_detected_in_cluster(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value=""):
            result = confirm_in_cluster(con, detected=True)
        assert result is True

    def test_default_accepts_detected_out_of_cluster(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value=""):
            result = confirm_in_cluster(con, detected=False)
        assert result is False

    def test_n_overrides_detected_true(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value="n"):
            result = confirm_in_cluster(con, detected=True)
        assert result is False

    def test_no_overrides_detected_true(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value="no"):
            result = confirm_in_cluster(con, detected=True)
        assert result is False

    def test_y_overrides_detected_false(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value="y"):
            result = confirm_in_cluster(con, detected=False)
        assert result is True

    def test_yes_overrides_detected_false(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value="yes"):
            result = confirm_in_cluster(con, detected=False)
        assert result is True

    def test_displays_detected_yes_message(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value=""):
            confirm_in_cluster(con, detected=True)
        assert con.has_any("auto-detected: yes")

    def test_displays_detected_no_message(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value=""):
            confirm_in_cluster(con, detected=False)
        assert con.has_any("auto-detected: no")


# ===========================================================================
# pick_context
# ===========================================================================


class TestPickContext:
    def test_empty_contexts_raises(self) -> None:
        con = FakeConsoleAdapter()
        with pytest.raises(K8sError, match="No kubeconfig contexts available"):
            pick_context(con, [], None)

    def test_default_selects_current(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("prod", is_current=True), _ctx("dev")]
        with patch("builtins.input", return_value=""):
            result = pick_context(con, ctxs, ctxs[0])
        assert result == "prod"

    def test_default_selects_first_when_no_current(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("staging"), _ctx("local")]
        with patch("builtins.input", return_value=""):
            result = pick_context(con, ctxs, None)
        assert result == "staging"

    def test_numbered_selection(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("prod"), _ctx("staging"), _ctx("local")]
        with patch("builtins.input", return_value="3"):
            result = pick_context(con, ctxs, None)
        assert result == "local"

    def test_invalid_input_falls_back_to_default(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("prod", is_current=True), _ctx("dev")]
        with patch("builtins.input", return_value="abc"):
            result = pick_context(con, ctxs, ctxs[0])
        assert result == "prod"

    def test_out_of_range_falls_back_to_default(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("prod", is_current=True)]
        with patch("builtins.input", return_value="99"):
            result = pick_context(con, ctxs, ctxs[0])
        assert result == "prod"

    def test_displays_current_marker(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("prod", is_current=True), _ctx("dev")]
        with patch("builtins.input", return_value=""):
            pick_context(con, ctxs, ctxs[0])
        assert con.has_any("[current]")

    def test_single_context_returns_it_on_enter(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("only-ctx")]
        with patch("builtins.input", return_value=""):
            result = pick_context(con, ctxs, None)
        assert result == "only-ctx"


# ===========================================================================
# pick_namespace
# ===========================================================================


class TestPickNamespace:
    def test_empty_namespaces_returns_default(self) -> None:
        con = FakeConsoleAdapter()
        result = pick_namespace(con, [], "orb-system")
        assert result == "orb-system"

    def test_default_pre_selected(self) -> None:
        con = FakeConsoleAdapter()
        nss = [_ns("default"), _ns("orb-system")]
        with patch("builtins.input", return_value=""):
            result = pick_namespace(con, nss, "orb-system")
        assert result == "orb-system"

    def test_numbered_selection(self) -> None:
        con = FakeConsoleAdapter()
        nss = [_ns("default"), _ns("orb-system"), _ns("kube-system")]
        with patch("builtins.input", return_value="3"):
            result = pick_namespace(con, nss, "default")
        assert result == "kube-system"

    def test_invalid_input_falls_back_to_default(self) -> None:
        con = FakeConsoleAdapter()
        nss = [_ns("default"), _ns("orb-system")]
        with patch("builtins.input", return_value="xyz"):
            result = pick_namespace(con, nss, "default")
        assert result == "default"

    def test_out_of_range_falls_back_to_default(self) -> None:
        con = FakeConsoleAdapter()
        nss = [_ns("default")]
        with patch("builtins.input", return_value="99"):
            result = pick_namespace(con, nss, "default")
        assert result == "default"

    def test_displays_selected_marker_for_default(self) -> None:
        con = FakeConsoleAdapter()
        nss = [_ns("default"), _ns("orb-system")]
        with patch("builtins.input", return_value=""):
            pick_namespace(con, nss, "orb-system")
        assert con.has_any("[selected]")

    def test_single_namespace_accepts_enter(self) -> None:
        con = FakeConsoleAdapter()
        nss = [_ns("orb-system")]
        with patch("builtins.input", return_value=""):
            result = pick_namespace(con, nss, "orb-system")
        assert result == "orb-system"


# ===========================================================================
# pick_service_account
# ===========================================================================


class TestPickServiceAccount:
    def test_empty_sas_returns_empty_string(self) -> None:
        con = FakeConsoleAdapter()
        result = pick_service_account(con, [])
        assert result == ""

    def test_skip_on_empty_input(self) -> None:
        con = FakeConsoleAdapter()
        sas = [_sa("default"), _sa("orb-runner")]
        with patch("builtins.input", return_value=""):
            result = pick_service_account(con, sas)
        assert result == ""

    def test_numbered_selection(self) -> None:
        con = FakeConsoleAdapter()
        sas = [_sa("default"), _sa("orb-runner")]
        with patch("builtins.input", return_value="2"):
            result = pick_service_account(con, sas)
        assert result == "orb-runner"

    def test_invalid_input_returns_empty(self) -> None:
        con = FakeConsoleAdapter()
        sas = [_sa("default")]
        with patch("builtins.input", return_value="abc"):
            result = pick_service_account(con, sas)
        assert result == ""

    def test_out_of_range_returns_empty(self) -> None:
        con = FakeConsoleAdapter()
        sas = [_sa("default")]
        with patch("builtins.input", return_value="99"):
            result = pick_service_account(con, sas)
        assert result == ""

    def test_displays_current_marker_for_default(self) -> None:
        con = FakeConsoleAdapter()
        sas = [_sa("default"), _sa("orb-runner")]
        with patch("builtins.input", return_value=""):
            pick_service_account(con, sas, default="default")
        assert con.has_any("[current]")

    def test_first_sa_selectable(self) -> None:
        con = FakeConsoleAdapter()
        sas = [_sa("orb-runner"), _sa("batch-worker")]
        with patch("builtins.input", return_value="1"):
            result = pick_service_account(con, sas)
        assert result == "orb-runner"


# ===========================================================================
# pick_image_pull_secret
# ===========================================================================


class TestPickImagePullSecret:
    def test_empty_secrets_returns_none(self) -> None:
        con = FakeConsoleAdapter()
        result = pick_image_pull_secret(con, [])
        assert result is None

    def test_none_on_empty_input(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value=""):
            result = pick_image_pull_secret(con, ["ecr-pull", "ghcr-token"])
        assert result is None

    def test_none_option_by_number(self) -> None:
        con = FakeConsoleAdapter()
        secrets = ["ecr-pull", "ghcr-token"]
        none_index = str(len(secrets) + 1)
        with patch("builtins.input", return_value=none_index):
            result = pick_image_pull_secret(con, secrets)
        assert result is None

    def test_numbered_selection(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value="1"):
            result = pick_image_pull_secret(con, ["ecr-pull", "ghcr-token"])
        assert result == "ecr-pull"

    def test_second_secret_selectable(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value="2"):
            result = pick_image_pull_secret(con, ["ecr-pull", "ghcr-token"])
        assert result == "ghcr-token"

    def test_invalid_input_returns_none(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value="xyz"):
            result = pick_image_pull_secret(con, ["ecr-pull"])
        assert result is None

    def test_out_of_range_returns_none(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value="99"):
            result = pick_image_pull_secret(con, ["ecr-pull"])
        assert result is None

    def test_displays_none_option(self) -> None:
        con = FakeConsoleAdapter()
        with patch("builtins.input", return_value=""):
            pick_image_pull_secret(con, ["ecr-pull"])
        assert con.has_any("none")


# ===========================================================================
# display_rbac_probe
# ===========================================================================


class TestDisplayRbacProbe:
    def test_all_granted_returns_true(self) -> None:
        con = FakeConsoleAdapter()
        result = display_rbac_probe(con, _rbac())
        assert result is True

    def test_all_granted_displays_success(self) -> None:
        con = FakeConsoleAdapter()
        display_rbac_probe(con, _rbac())
        assert "success" in con.printed_methods()

    def test_all_granted_no_input_prompt(self) -> None:
        """When all permissions granted, no input() is called."""
        con = FakeConsoleAdapter()
        with patch("builtins.input") as mock_input:
            display_rbac_probe(con, _rbac())
        mock_input.assert_not_called()

    def test_partial_deny_y_continues(self) -> None:
        con = FakeConsoleAdapter()
        rbac = _rbac(create=False)
        with patch("builtins.input", return_value="y"):
            result = display_rbac_probe(con, rbac, namespace="orb-system", sa="orb-runner")
        assert result is True

    def test_partial_deny_yes_continues(self) -> None:
        con = FakeConsoleAdapter()
        rbac = _rbac(create=False)
        with patch("builtins.input", return_value="yes"):
            result = display_rbac_probe(con, rbac)
        assert result is True

    def test_partial_deny_n_aborts(self) -> None:
        con = FakeConsoleAdapter()
        rbac = _rbac(delete=False)
        with patch("builtins.input", return_value="n"):
            result = display_rbac_probe(con, rbac)
        assert result is False

    def test_partial_deny_empty_aborts(self) -> None:
        """Default is N (abort) — empty Enter should return False."""
        con = FakeConsoleAdapter()
        rbac = _rbac(watch=False)
        with patch("builtins.input", return_value=""):
            result = display_rbac_probe(con, rbac)
        assert result is False

    def test_partial_deny_shows_kubectl_remediation(self) -> None:
        con = FakeConsoleAdapter()
        rbac = _rbac(create=False)
        with patch("builtins.input", return_value="y"):
            display_rbac_probe(con, rbac, namespace="orb-system", sa="orb-runner")
        assert con.has_any("kubectl create rolebinding")

    def test_partial_deny_shows_namespace_in_remediation(self) -> None:
        con = FakeConsoleAdapter()
        rbac = _rbac(create=False)
        with patch("builtins.input", return_value="y"):
            display_rbac_probe(con, rbac, namespace="my-namespace", sa="my-sa")
        assert con.has_any("my-namespace")

    def test_all_denied_shows_all_verbs(self) -> None:
        con = FakeConsoleAdapter()
        rbac = _rbac(create=False, watch=False, delete=False)
        with patch("builtins.input", return_value="y"):
            display_rbac_probe(con, rbac)
        joined = " ".join(con.printed())
        assert "create pods" in joined
        assert "watch pods" in joined
        assert "delete pods" in joined


# ===========================================================================
# discover_infrastructure_interactive — end-to-end composition
# ===========================================================================


class TestDiscoverInfrastructureInteractive:
    # Lean return: only operator-chosen leaves are present
    _REQUIRED_KEYS = {"in_cluster", "namespace"}
    _BANNED_SCAFFOLD_KEYS = {
        "contexts",
        "current_context",
        "cluster_endpoint",
        "namespaces",
        "service_accounts",
        "image_pull_secrets",
        "rbac_probe",
        "provider",
        "default_namespace",
        "chosen_service_account",
        "chosen_image_pull_secret",
    }

    def _inputs(self, *values: str):
        """Return side_effect list for input() from positional string args."""
        return list(values)

    def test_happy_path_out_of_cluster_returns_lean_keys(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("prod", is_current=True)]
        nss = [_ns("default")]
        sas = [_sa("default")]
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=ctxs,
            current_context=ctxs[0],
            namespaces=nss,
            service_accounts=sas,
            pull_secrets=["ecr-pull"],
        )
        # Inputs: confirm=<enter>, namespace=<enter>, sa=<enter>, secret=<enter>
        # (context no longer prompted — taken from provider_config or current_context)
        with patch("builtins.input", side_effect=self._inputs("", "", "", "")):
            result = svc.discover_infrastructure_interactive({"name": "test"})

        assert self._REQUIRED_KEYS.issubset(result.keys())
        # No scaffold keys must leak into the return
        leaked = set(result.keys()) & self._BANNED_SCAFFOLD_KEYS
        assert not leaked, f"Scaffold keys leaked: {leaked}"

    def test_in_cluster_skips_context_prompt(self) -> None:
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=True,
            namespaces=[_ns("orb-system")],
            service_accounts=[_sa("default")],
            pull_secrets=[],
        )
        # in-cluster confirm=y, namespace=<enter>, sa=<enter>, pull-secret skipped (empty list)
        with patch("builtins.input", side_effect=self._inputs("y", "", "")):
            result = svc.discover_infrastructure_interactive({})

        assert result["in_cluster"] is True
        assert "context" not in result
        # discover_contexts must NOT have been called.
        svc.discover_contexts.assert_not_called()  # type: ignore[attr-defined]

    def test_context_selection_calls_discover_contexts(self) -> None:
        con = FakeConsoleAdapter()
        ctxs = [_ctx("prod", is_current=True), _ctx("dev")]
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=ctxs,
            current_context=ctxs[0],
            namespaces=[_ns("default")],
            service_accounts=[_sa("default")],
            pull_secrets=[],
        )
        # Inputs: confirm=<enter>, namespace=<enter>, sa=<enter>
        # (context no longer prompted; discover_contexts is still called for the
        # current_context fallback and cluster-endpoint display)
        with patch("builtins.input", side_effect=self._inputs("", "", "")):
            result = svc.discover_infrastructure_interactive({})

        svc.discover_contexts.assert_called_once()  # type: ignore[attr-defined]
        # context is a single chosen string — not the full list
        assert result.get("context") == "prod"
        assert "contexts" not in result

    def test_sa_403_auto_skips_and_shows_notice(self) -> None:
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=[_ctx("prod", is_current=True)],
            current_context=_ctx("prod", is_current=True),
            namespaces=[_ns("default")],
            service_accounts=[],  # empty = 403 fallback
            pull_secrets=[],
        )
        # Inputs: confirm=<enter>, namespace=<enter>
        # (context no longer prompted; SA auto-skipped when empty)
        with patch("builtins.input", side_effect=self._inputs("", "")):
            result = svc.discover_infrastructure_interactive({})

        # No service_account key when nothing was picked
        assert "service_account" not in result
        assert con.has_any("service_account") or con.has_any("ServiceAccounts")

    def test_rbac_all_granted_no_abort_prompt(self) -> None:
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=[_ctx("prod", is_current=True)],
            current_context=_ctx("prod", is_current=True),
            namespaces=[_ns("default")],
            service_accounts=[_sa("default")],
            pull_secrets=[],
            rbac=_rbac(create=True, watch=True, delete=True),
        )
        # Inputs: confirm=<enter>, namespace=<enter>, sa=<enter>
        # (context no longer prompted); pull_secrets empty so no secret prompt;
        # RBAC granted so no Continue? prompt
        with patch("builtins.input", side_effect=self._inputs("", "", "")):
            result = svc.discover_infrastructure_interactive({})

        # rbac_probe must not be in the lean return
        assert "rbac_probe" not in result
        assert "in_cluster" in result

    def test_rbac_failure_abort_raises(self) -> None:
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=[_ctx("prod", is_current=True)],
            current_context=_ctx("prod", is_current=True),
            namespaces=[_ns("default")],
            service_accounts=[_sa("default")],
            pull_secrets=[],
            rbac=_rbac(create=False),
        )
        # Inputs: confirm=<enter>, namespace=<enter>, sa=<enter>,
        #         rbac-continue=n  (context no longer prompted; no pull-secret — empty list)
        with patch("builtins.input", side_effect=self._inputs("", "", "", "n")):
            with pytest.raises(K8sDiscoveryError, match="aborted"):
                svc.discover_infrastructure_interactive({})

    def test_rbac_failure_continue_does_not_raise(self) -> None:
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=[_ctx("prod", is_current=True)],
            current_context=_ctx("prod", is_current=True),
            namespaces=[_ns("default")],
            service_accounts=[_sa("default")],
            pull_secrets=[],
            rbac=_rbac(create=False),
        )
        # Inputs: namespace=<enter>, sa=<enter>, rbac-continue=y
        # (confirm_in_cluster removed; context no longer prompted; no pull-secret — empty list)
        with patch("builtins.input", side_effect=self._inputs("", "", "y")):
            result = svc.discover_infrastructure_interactive({})

        # rbac_probe is diagnostic only — not in the lean return
        assert "rbac_probe" not in result
        assert "in_cluster" in result

    def test_pull_secret_selected_stored_in_image_pull_secret_field(self) -> None:
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=[_ctx("prod", is_current=True)],
            current_context=_ctx("prod", is_current=True),
            namespaces=[_ns("default")],
            service_accounts=[_sa("default")],
            pull_secrets=["ecr-pull", "ghcr-token"],
        )
        # Inputs: namespace=<enter>, sa=<enter>, secret=select "2"=ghcr-token
        # (confirm_in_cluster removed; context no longer prompted); RBAC all granted → no continue prompt
        with patch("builtins.input", side_effect=self._inputs("", "", "2")):
            result = svc.discover_infrastructure_interactive({})

        assert result.get("image_pull_secret") == "ghcr-token"
        # Old key must be gone
        assert "chosen_image_pull_secret" not in result

    def test_chosen_sa_stored_in_service_account_field(self) -> None:
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=[_ctx("prod", is_current=True)],
            current_context=_ctx("prod", is_current=True),
            namespaces=[_ns("default")],
            service_accounts=[_sa("default"), _sa("orb-runner")],
            pull_secrets=[],
        )
        # Inputs: namespace=<enter>, sa=select "2"=orb-runner
        # (confirm_in_cluster removed; context no longer prompted); no pull-secret prompt (empty list);
        # RBAC all granted → no continue prompt
        with patch("builtins.input", side_effect=self._inputs("", "2")):
            result = svc.discover_infrastructure_interactive({})

        assert result.get("service_account") == "orb-runner"
        # Old key must be gone
        assert "chosen_service_account" not in result

    def test_namespace_auto_selected_on_single_item_sa_bound(self) -> None:
        """When discover_namespaces returns only the SA-bound ns, no prompt shown."""
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=True,
            namespaces=[],  # 403 fallback path — empty returned, sa_bound file used
            service_accounts=[],
            pull_secrets=[],
        )
        # Patch SA namespace file.
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "orb-system"

        with patch(
            "orb.providers.k8s.services.infrastructure_discovery_service._SA_NAMESPACE_FILE",
            mock_path,
        ):
            # in-cluster confirm=y, sa step skipped (empty), no pull-secret
            with patch("builtins.input", side_effect=self._inputs("y")):
                result = svc.discover_infrastructure_interactive({"name": "mycluster"})

        assert result["namespace"] == "orb-system"
        # Old key must be gone
        assert "default_namespace" not in result

    def test_rbac_probe_exception_returns_lean_result(self) -> None:
        """RBAC probe failure: operator continues; probe dict never appears in return."""
        con = FakeConsoleAdapter()
        svc = _make_fully_mocked_service(
            console=con,
            in_cluster=False,
            contexts=[_ctx("prod", is_current=True)],
            current_context=_ctx("prod", is_current=True),
            namespaces=[_ns("default")],
            service_accounts=[_sa("default")],
            pull_secrets=[],
        )
        svc.probe_rbac = MagicMock(side_effect=K8sDiscoveryError("self-review blocked"))  # type: ignore[method-assign]

        # Inputs: namespace=<enter>, sa=<enter>, rbac-continue=y
        # (confirm_in_cluster removed; context no longer prompted; RBAC exception → all-False → Continue? prompt)
        with patch("builtins.input", side_effect=self._inputs("", "", "y")):
            result = svc.discover_infrastructure_interactive({})

        assert "rbac_probe" not in result
        assert "in_cluster" in result

    def test_full_flow_no_provider_key_in_lean_return(self) -> None:
        """provider_name is passed in but never written to the lean return dict."""
        con = FakeConsoleAdapter()
        ctxs = [_ctx("prod", is_current=True)]
        svc = _make_fully_mocked_service(
            console=con,
            contexts=ctxs,
            current_context=ctxs[0],
            namespaces=[_ns("default")],
            service_accounts=[_sa("default")],
            pull_secrets=[],
        )
        # Inputs: confirm=<enter>, namespace=<enter>, sa=<enter>
        # (context no longer prompted)
        with patch("builtins.input", side_effect=self._inputs("", "", "")):
            result = svc.discover_infrastructure_interactive({"name": "my-cluster"})

        # "provider" is a scaffold key — must not appear in the lean return
        assert "provider" not in result
