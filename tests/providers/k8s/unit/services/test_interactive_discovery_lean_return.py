"""Tests for the lean return shape of discover_infrastructure_interactive.

Verifies that the interactive discovery wizard returns only the operator's
chosen leaf values — no scaffold lists, diagnostics, or empty strings — and
that the strategy classifier routes those leaves to the correct config sections.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.exceptions.k8s_exceptions import K8sDiscoveryError
from orb.providers.k8s.services.discovery_models import (
    KubeContextInfo,
    NamespaceInfo,
    RBACProbeResult,
    ServiceAccountInfo,
)
from orb.providers.k8s.services.infrastructure_discovery_service import (
    K8sInfrastructureDiscoveryService,
)
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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

_ALLOWED_KEYS = {"in_cluster", "namespace", "context", "service_account", "image_pull_secret"}


def _make_service(
    namespace: str = "default",
    in_cluster_cfg: bool = False,
) -> K8sInfrastructureDiscoveryService:
    config = K8sProviderConfig(namespace=namespace, in_cluster=in_cluster_cfg)
    return K8sInfrastructureDiscoveryService(
        config=config,
        logger=MagicMock(),
        console=MagicMock(),
    )


def _rbac_all_ok(ns: str = "default") -> RBACProbeResult:
    return RBACProbeResult(
        namespace=ns,
        can_create_pods=True,
        can_watch_pods=True,
        can_delete_pods=True,
    )


def _stub_out_of_cluster_service(
    chosen_context: str = "prod",
    chosen_namespace: str = "orb-system",
    chosen_sa: str = "orb-runner",
    chosen_secret: str = "ecr-pull",
) -> K8sInfrastructureDiscoveryService:
    """Return a service with all leaf methods mocked for an out-of-cluster scenario."""
    svc = _make_service()
    svc.detect_in_cluster = MagicMock(return_value=False)  # type: ignore[method-assign]
    svc.discover_contexts = MagicMock(  # type: ignore[method-assign]
        return_value=(
            [
                KubeContextInfo(
                    name=chosen_context, cluster="c1", user="u1", namespace=None, is_current=True
                )
            ],
            KubeContextInfo(
                name=chosen_context, cluster="c1", user="u1", namespace=None, is_current=True
            ),
        )
    )
    svc.discover_cluster_endpoint = MagicMock(return_value="https://example.k8s:6443")  # type: ignore[method-assign]
    svc.discover_namespaces = MagicMock(  # type: ignore[method-assign]
        return_value=[NamespaceInfo(name=chosen_namespace, status="Active", age_days=0)]
    )
    svc.discover_service_accounts = MagicMock(  # type: ignore[method-assign]
        return_value=[
            ServiceAccountInfo(name=chosen_sa, namespace=chosen_namespace, secrets_count=1)
        ]
    )
    svc.discover_image_pull_secrets = MagicMock(return_value=[chosen_secret])  # type: ignore[method-assign]
    svc.probe_rbac = MagicMock(return_value=_rbac_all_ok(chosen_namespace))  # type: ignore[method-assign]
    return svc


def _run_interactive(
    svc: K8sInfrastructureDiscoveryService,
    *,
    namespace_pick: str = "1",
    sa_pick: str = "1",
    secret_pick: str = "1",
    in_cluster_confirm: str = "",
    provider_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Drive discover_infrastructure_interactive with canned input() answers.

    The context-selection prompt was removed from the interactive wizard;
    only in_cluster_confirm, namespace_pick, sa_pick, and secret_pick remain.
    The kubeconfig context is taken from provider_config["config"]["profile"]
    (or from the service's own config) without re-prompting.
    """
    answers = iter([in_cluster_confirm, namespace_pick, sa_pick, secret_pick])
    with patch(
        "orb.providers.k8s.services.init_prompts.input",
        side_effect=lambda _prompt: next(answers, ""),
    ):
        return svc.discover_infrastructure_interactive(provider_config or {})


# ---------------------------------------------------------------------------
# test_discover_returns_only_chosen_leaves
# ---------------------------------------------------------------------------


class TestDiscoverReturnsOnlyChosenLeaves:
    def test_no_scaffold_keys_in_return(self) -> None:
        svc = _stub_out_of_cluster_service()
        result = _run_interactive(svc, in_cluster_confirm="n")
        leaked = set(result.keys()) & _BANNED_SCAFFOLD_KEYS
        assert not leaked, f"Scaffold keys leaked into return: {leaked}"

    def test_all_return_keys_are_allowed(self) -> None:
        svc = _stub_out_of_cluster_service()
        result = _run_interactive(svc, in_cluster_confirm="n")
        unexpected = set(result.keys()) - _ALLOWED_KEYS
        assert not unexpected, f"Unexpected keys in return: {unexpected}"

    def test_contains_in_cluster_and_namespace(self) -> None:
        svc = _stub_out_of_cluster_service(chosen_namespace="orb-system")
        result = _run_interactive(svc, in_cluster_confirm="n")
        assert "in_cluster" in result
        assert "namespace" in result
        assert result["namespace"] == "orb-system"

    def test_contains_chosen_service_account(self) -> None:
        svc = _stub_out_of_cluster_service(chosen_sa="orb-runner")
        result = _run_interactive(svc, in_cluster_confirm="n")
        assert result.get("service_account") == "orb-runner"

    def test_contains_chosen_image_pull_secret(self) -> None:
        svc = _stub_out_of_cluster_service(chosen_secret="ecr-pull")
        result = _run_interactive(svc, in_cluster_confirm="n")
        assert result.get("image_pull_secret") == "ecr-pull"


# ---------------------------------------------------------------------------
# test_in_cluster_path_returns_only_in_cluster_namespace
# ---------------------------------------------------------------------------


class TestInClusterPath:
    def _make_in_cluster_service(
        self, chosen_namespace: str = "team-ns"
    ) -> K8sInfrastructureDiscoveryService:
        svc = _make_service()
        svc.detect_in_cluster = MagicMock(return_value=True)  # type: ignore[method-assign]
        svc.discover_namespaces = MagicMock(  # type: ignore[method-assign]
            return_value=[NamespaceInfo(name=chosen_namespace, status="Active", age_days=0)]
        )
        svc.discover_service_accounts = MagicMock(return_value=[])  # type: ignore[method-assign]
        svc.discover_image_pull_secrets = MagicMock(return_value=[])  # type: ignore[method-assign]
        svc.probe_rbac = MagicMock(return_value=_rbac_all_ok(chosen_namespace))  # type: ignore[method-assign]
        return svc

    def test_in_cluster_no_context_key(self) -> None:
        svc = self._make_in_cluster_service()
        answers = iter(["y", "1"])  # confirm in-cluster, pick namespace
        with patch(
            "orb.providers.k8s.services.init_prompts.input",
            side_effect=lambda _p: next(answers, ""),
        ):
            result = svc.discover_infrastructure_interactive({})
        assert "context" not in result
        assert result.get("in_cluster") is True

    def test_in_cluster_namespace_is_single_string(self) -> None:
        svc = self._make_in_cluster_service("team-ns")
        answers = iter(["y", "1"])
        with patch(
            "orb.providers.k8s.services.init_prompts.input",
            side_effect=lambda _p: next(answers, ""),
        ):
            result = svc.discover_infrastructure_interactive({})
        assert isinstance(result["namespace"], str)
        assert result["namespace"] == "team-ns"

    def test_no_scaffold_keys_in_cluster(self) -> None:
        svc = self._make_in_cluster_service()
        answers = iter(["y", "1"])
        with patch(
            "orb.providers.k8s.services.init_prompts.input",
            side_effect=lambda _p: next(answers, ""),
        ):
            result = svc.discover_infrastructure_interactive({})
        leaked = set(result.keys()) & _BANNED_SCAFFOLD_KEYS
        assert not leaked


# ---------------------------------------------------------------------------
# test_out_of_cluster_path_returns_context (not contexts plural)
# ---------------------------------------------------------------------------


class TestOutOfClusterContext:
    def test_returns_context_singular_string(self) -> None:
        svc = _stub_out_of_cluster_service(chosen_context="staging")
        result = _run_interactive(svc, in_cluster_confirm="n")
        assert "context" in result
        assert isinstance(result["context"], str)
        assert result["context"] == "staging"
        assert "contexts" not in result

    def test_context_value_matches_pick(self) -> None:
        svc = _stub_out_of_cluster_service(chosen_context="dev")
        result = _run_interactive(svc, in_cluster_confirm="n")
        assert result["context"] == "dev"


# ---------------------------------------------------------------------------
# test_namespace_pick_returns_single_value_not_list
# ---------------------------------------------------------------------------


class TestNamespacePickIsSingleValue:
    def test_namespace_is_str_not_list(self) -> None:
        svc = _stub_out_of_cluster_service(chosen_namespace="ml-jobs")
        result = _run_interactive(svc, in_cluster_confirm="n")
        assert isinstance(result["namespace"], str)
        assert result["namespace"] == "ml-jobs"
        assert "namespaces" not in result

    def test_default_namespace_key_absent(self) -> None:
        svc = _stub_out_of_cluster_service()
        result = _run_interactive(svc, in_cluster_confirm="n")
        assert "default_namespace" not in result


# ---------------------------------------------------------------------------
# test_rbac_probe_displayed_but_not_in_return
# ---------------------------------------------------------------------------


class TestRbacProbeDisplayedNotInReturn:
    def test_rbac_probe_not_in_return(self) -> None:
        svc = _stub_out_of_cluster_service()
        result = _run_interactive(svc, in_cluster_confirm="n")
        assert "rbac_probe" not in result

    def test_rbac_denied_shows_prompt_and_aborts_on_no(self) -> None:
        svc = _stub_out_of_cluster_service()
        svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
            return_value=RBACProbeResult(
                namespace="orb-system",
                can_create_pods=False,
                can_watch_pods=True,
                can_delete_pods=True,
            )
        )
        # Answers: in-cluster=n, namespace=1, sa=1, secret=1, rbac-continue=N
        # (context pick removed — context comes from provider_config or current_context)
        answers = iter(["n", "1", "1", "1", "N"])
        with patch(
            "orb.providers.k8s.services.init_prompts.input",
            side_effect=lambda _p: next(answers, ""),
        ):
            with pytest.raises(K8sDiscoveryError, match="aborted"):
                svc.discover_infrastructure_interactive({})

    def test_rbac_denied_continues_when_operator_says_yes(self) -> None:
        svc = _stub_out_of_cluster_service()
        svc.probe_rbac = MagicMock(  # type: ignore[method-assign]
            return_value=RBACProbeResult(
                namespace="orb-system",
                can_create_pods=False,
                can_watch_pods=True,
                can_delete_pods=True,
            )
        )
        # Answers: namespace=1, sa=1, secret=1, rbac-continue=y
        # (confirm_in_cluster removed; context resolved from config without prompting)
        answers = iter(["1", "1", "1", "y"])
        with patch(
            "orb.providers.k8s.services.init_prompts.input",
            side_effect=lambda _p: next(answers, ""),
        ):
            result = svc.discover_infrastructure_interactive({})
        assert "rbac_probe" not in result
        assert "in_cluster" in result


# ---------------------------------------------------------------------------
# test_classifier_routes_context_in_cluster_namespace_to_config
# ---------------------------------------------------------------------------


class TestClassifierRoutesConnectionKeys:
    def test_get_cli_extra_config_keys_contains_connection_keys(self) -> None:
        strategy = K8sProviderStrategy(
            config=K8sProviderConfig(),
            logger=MagicMock(),
            kubernetes_client=MagicMock(),
        )
        keys = strategy.get_cli_extra_config_keys()
        assert "context" in keys
        assert "in_cluster" in keys
        assert "namespace" in keys

    def test_service_account_not_in_config_keys(self) -> None:
        strategy = K8sProviderStrategy(
            config=K8sProviderConfig(),
            logger=MagicMock(),
            kubernetes_client=MagicMock(),
        )
        keys = strategy.get_cli_extra_config_keys()
        assert "service_account" not in keys

    def test_image_pull_secret_not_in_config_keys(self) -> None:
        strategy = K8sProviderStrategy(
            config=K8sProviderConfig(),
            logger=MagicMock(),
            kubernetes_client=MagicMock(),
        )
        keys = strategy.get_cli_extra_config_keys()
        assert "image_pull_secret" not in keys

    def test_default_image_pull_secret_removed(self) -> None:
        """Old key 'default_image_pull_secret' must no longer appear."""
        strategy = K8sProviderStrategy(
            config=K8sProviderConfig(),
            logger=MagicMock(),
            kubernetes_client=MagicMock(),
        )
        keys = strategy.get_cli_extra_config_keys()
        assert "default_image_pull_secret" not in keys


# ---------------------------------------------------------------------------
# test_classifier_routes_service_account_image_pull_secret_to_template_defaults
# ---------------------------------------------------------------------------


class TestClassifierRoutesTemplateDefaultKeys:
    """Simulate the init_command_handler classifier loop against a recorded discovery dict."""

    def _apply_classifier(
        self,
        infrastructure_defaults: dict[str, Any],
        config_only_keys: set[str],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Mirrors the classifier logic in init_command_handler lines 684-696."""
        template_level = {
            k: v for k, v in infrastructure_defaults.items() if k not in config_only_keys
        }
        config_level = {
            k: infrastructure_defaults[k] for k in config_only_keys if k in infrastructure_defaults
        }
        return template_level, config_level

    def test_out_of_cluster_full_pick_routes_correctly(self) -> None:
        strategy = K8sProviderStrategy(
            config=K8sProviderConfig(),
            logger=MagicMock(),
            kubernetes_client=MagicMock(),
        )
        # Simulated lean return from discover_infrastructure_interactive
        discovery_result = {
            "in_cluster": False,
            "namespace": "ml-jobs",
            "context": "prod",
            "service_account": "orb-runner",
            "image_pull_secret": "ecr-pull",
        }
        config_only_keys = strategy.get_cli_extra_config_keys()
        template_defaults, config_extras = self._apply_classifier(
            discovery_result, config_only_keys
        )

        # Connection-level keys go to config
        assert config_extras.get("context") == "prod"
        assert config_extras.get("in_cluster") is False
        assert config_extras.get("namespace") == "ml-jobs"

        # Template-default keys go to template_defaults
        assert template_defaults.get("service_account") == "orb-runner"
        assert template_defaults.get("image_pull_secret") == "ecr-pull"

        # No cross-contamination
        assert "context" not in template_defaults
        assert "namespace" not in template_defaults
        assert "service_account" not in config_extras
        assert "image_pull_secret" not in config_extras

    def test_minimal_in_cluster_pick_routes_correctly(self) -> None:
        strategy = K8sProviderStrategy(
            config=K8sProviderConfig(),
            logger=MagicMock(),
            kubernetes_client=MagicMock(),
        )
        discovery_result = {
            "in_cluster": True,
            "namespace": "default",
        }
        config_only_keys = strategy.get_cli_extra_config_keys()
        template_defaults, config_extras = self._apply_classifier(
            discovery_result, config_only_keys
        )

        assert config_extras.get("in_cluster") is True
        assert config_extras.get("namespace") == "default"
        assert template_defaults == {}


# ---------------------------------------------------------------------------
# test_discover_infrastructure_does_not_call_pick_context
# test_discover_infrastructure_uses_config_context_not_prompt
# ---------------------------------------------------------------------------


class TestDiscoverDoesNotCallPickContext:
    """pick_context must never be called during discover_infrastructure_interactive."""

    def test_discover_infrastructure_does_not_call_pick_context(self) -> None:
        svc = _stub_out_of_cluster_service(chosen_context="prod")
        with patch("orb.providers.k8s.services.init_prompts.pick_context") as mock_pick_context:
            answers = iter(["n", "1", "1", "1"])
            with patch(
                "orb.providers.k8s.services.init_prompts.input",
                side_effect=lambda _p: next(answers, ""),
            ):
                svc.discover_infrastructure_interactive({})
        mock_pick_context.assert_not_called()

    def test_discover_infrastructure_uses_config_context_not_prompt(self) -> None:
        """Context from provider_config["config"]["profile"] is used directly."""
        svc = _stub_out_of_cluster_service(chosen_context="ignored")
        with patch("orb.providers.k8s.services.init_prompts.pick_context") as mock_pick_context:
            answers = iter(["n", "1", "1", "1"])
            with patch(
                "orb.providers.k8s.services.init_prompts.input",
                side_effect=lambda _p: next(answers, ""),
            ):
                result = svc.discover_infrastructure_interactive(
                    {"config": {"profile": "pre-selected-context"}}
                )
        mock_pick_context.assert_not_called()
        # The pre-selected context should appear in the result
        assert result.get("context") == "pre-selected-context"
