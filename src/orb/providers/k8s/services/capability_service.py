"""K8s Capability Service — provider capabilities and CLI/credential helpers.

Extracted from :class:`K8sProviderStrategy` to mirror the AWS
``AWSCapabilityService`` pattern.  Houses every classmethod and property
that describes *what the provider supports* rather than *doing* work:

* ``get_capabilities``   — the ``ProviderCapabilities`` value object
* ``get_ui_column_schema``
* Credential and operational requirement declarations
* CLI config-key routing helpers
* Provider-name generation / parsing
* ``get_available_regions`` / ``get_default_region``

The strategy delegates all of these via ``self._capability_service`` so the
``K8sProviderStrategy`` class stays focused on lifecycle wiring.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import ProviderCapabilities, ProviderOperationType
from orb.providers.k8s.value_objects import KubernetesProviderApi

if TYPE_CHECKING:  # pragma: no cover
    pass

# Sentinel value used when the user selects the in-cluster ServiceAccount
# credential source.
_IN_CLUSTER_SENTINEL = "in_cluster"

_SUPPORTED_APIS: tuple[str, ...] = tuple(api.value for api in KubernetesProviderApi)


def _normalise_sentinel(value: str) -> str:
    """Normalise ``"in-cluster"`` → ``"in_cluster"`` for sentinel comparison."""
    return value.replace("-", "_").lower()


class K8sCapabilityService:
    """Service for k8s provider capabilities, naming, and CLI/credential helpers."""

    def __init__(self, logger: LoggingPort) -> None:
        self._logger = logger

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    def get_capabilities(self) -> ProviderCapabilities:
        """Return the provider's declared operation and API surface."""
        return ProviderCapabilities(
            provider_type="k8s",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.HEALTH_CHECK,
                # START / STOP are supported for Deployment and StatefulSet
                # workloads via spec.replicas scaling.  Pod and Job return
                # UNSUPPORTED_OPERATION_FOR_KIND at runtime — they are listed
                # here because the operation type IS handled (not blindly
                # rejected) and the caller needs to know it is wired.
                ProviderOperationType.START_INSTANCES,
                ProviderOperationType.STOP_INSTANCES,
            ],
            supported_apis=list(_SUPPORTED_APIS),
            features={
                "selective_termination": False,
                "selective_termination_by_api": {
                    "Pod": True,
                    "Deployment": True,
                    "StatefulSet": True,
                    "Job": False,
                },
                # START / STOP via spec.replicas scale are supported only for
                # controller-backed workloads.  The top-level flag is the
                # lowest-common-denominator (False, because Pod and Job cannot
                # be scaled) — mirroring selective_termination above.  Callers
                # that need the accurate per-workload answer must consult
                # start_stop_supported_by_api; Pod/Job return
                # UNSUPPORTED_OPERATION_FOR_KIND at runtime.
                "start_stop_supported": False,
                "start_stop_supported_by_api": {
                    "Pod": False,
                    "Deployment": True,
                    "StatefulSet": True,
                    "Job": False,
                },
                "watch_supported": True,
                "namespaces_supported": True,
            },
        )

    # ------------------------------------------------------------------
    # Provider naming
    # ------------------------------------------------------------------

    @staticmethod
    def generate_provider_name(config: dict[str, Any]) -> str:
        """Generate a Kubernetes provider instance name.

        Pattern: ``k8s_{sanitized_context}``.
        """
        raw_context: str | None = config.get("context") or None

        if raw_context:
            sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", raw_context)
            if sanitized == "in-cluster":
                sanitized = f"ctx_{sanitized}"
            return f"k8s_{sanitized}"

        return "k8s_in-cluster"

    @staticmethod
    def parse_provider_name(provider_name: str) -> dict[str, str]:
        """Inverse of :meth:`generate_provider_name`."""
        if not provider_name.startswith("k8s_"):
            return {}
        suffix = provider_name[len("k8s_") :]
        return {"context_or_namespace": suffix}

    @staticmethod
    def get_provider_name_pattern() -> str:
        """Return the naming pattern string."""
        return "k8s_{sanitized_context}"

    @staticmethod
    def get_supported_apis() -> list[str]:
        """Return the list of supported provider API values."""
        return list(_SUPPORTED_APIS)

    # ------------------------------------------------------------------
    # Region helpers (Kubernetes has no regions)
    # ------------------------------------------------------------------

    @staticmethod
    def get_available_regions() -> list[tuple[str, str]]:
        """Kubernetes has contexts, not regions — return an empty list."""
        return []

    @staticmethod
    def get_default_region() -> str:
        """Kubernetes has no region concept; return an empty string."""
        return ""

    # ------------------------------------------------------------------
    # CLI helpers
    # ------------------------------------------------------------------

    @staticmethod
    def get_cli_extra_config_keys() -> set[str]:
        """Return k8s keys that belong in provider config, not template_defaults."""
        return {"context", "in_cluster", "namespace"}

    @staticmethod
    def get_cli_infrastructure_defaults(args: Any) -> dict[str, Any]:
        """Extract k8s infrastructure defaults from parsed CLI args."""
        return {}

    @staticmethod
    def get_cli_provider_config(args: Any) -> dict[str, Any]:
        """Extract Kubernetes provider config keys from parsed CLI args."""
        result: dict[str, Any] = {}
        context = getattr(args, "kubernetes_context", None)
        if context is not None:
            result["context"] = context
        kubeconfig = getattr(args, "kubernetes_kubeconfig", None)
        if kubeconfig is not None:
            result["kubeconfig_path"] = kubeconfig
        namespace = getattr(args, "kubernetes_namespace", None)
        if namespace is not None:
            result["namespace"] = namespace
        return result

    @staticmethod
    def get_operational_param_choices(param: str) -> list[tuple[str, str]]:
        """Return picker choices for an operational parameter, if any."""
        return []

    @staticmethod
    def get_operational_param_default(param: str) -> str:
        """Return the default value for an operational parameter."""
        if param == "namespace":
            return "default"
        return ""

    # ------------------------------------------------------------------
    # Credentials
    # ------------------------------------------------------------------

    def get_available_credential_sources(self) -> list[dict]:
        """Return Kubernetes credential sources visible to ORB."""
        from orb.infrastructure.logging.logger import get_logger as _get_logger

        _log = _get_logger(__name__)
        sources: list[dict] = []

        try:
            from orb.providers.k8s.auth.in_cluster import is_in_cluster

            if is_in_cluster():
                sources.append(
                    {
                        "name": _IN_CLUSTER_SENTINEL,
                        "description": "in-cluster ServiceAccount",
                        "config_delta": {"in_cluster": True},
                    }
                )
        except Exception as exc:
            _log.debug("in_cluster detection failed: %s", exc)

        try:
            import kubernetes.config as _k8s_config

            contexts, current = _k8s_config.list_kube_config_contexts()
            current_name = current.get("name") if current else None
            for ctx in contexts or []:
                name = ctx.get("name", "")
                if not name:
                    continue
                marker = " (current)" if name == current_name else ""
                cluster = ctx.get("context", {}).get("cluster", "?")
                if name == cluster:
                    label = f"{name}{marker}"
                else:
                    label = f"{name} → {cluster}{marker}"
                sources.append(
                    {
                        "name": name,
                        "description": label,
                        "config_delta": {"context": name},
                    }
                )
        except Exception as exc:
            _log.debug("kubeconfig context enumeration failed: %s", exc)

        if not sources:
            sources.append(
                {
                    "name": "default",
                    "description": (
                        "Default credentials resolved by the kubernetes-client SDK "
                        "(in-cluster token or KUBECONFIG / ~/.kube/config)"
                    ),
                    "config_delta": {"context": "default"},
                }
            )

        return sources

    @staticmethod
    def test_credentials(credential_source: Optional[str] = None, **kwargs: Any) -> dict:
        """Verify the selected credentials can reach the apiserver."""
        try:
            import kubernetes.client as _k8s_client
            import kubernetes.config as _k8s_config
            from kubernetes.client.exceptions import ApiException
        except ImportError:
            return {
                "success": False,
                "error": (
                    "The kubernetes SDK is not installed.  Install with: pip install 'orb-py[k8s]'"
                ),
            }

        from orb.providers.k8s.auth.kubeconfig import (
            _force_non_interactive_exec,
            _install_non_interactive_refresh_hook_on,
        )

        try:
            # ``load_kube_config`` / ``load_config`` run the exec credential
            # plugin to mint the token, and its interactivity is decided by
            # ``sys.stdout.isatty()``; force the non-interactive branch so a
            # TTY-attached ``orb`` diagnostic attaches the token.  The lazy
            # re-mint hook is wrapped afterwards so the ``get_api_resources``
            # call below stays non-interactive if the token has expired.
            if (
                credential_source is not None
                and _normalise_sentinel(credential_source) == _IN_CLUSTER_SENTINEL
            ):
                _k8s_config.load_incluster_config()
                context_label = "in-cluster"
            elif credential_source and credential_source not in ("default", ""):
                with _force_non_interactive_exec():
                    _k8s_config.load_kube_config(context=credential_source)
                _install_non_interactive_refresh_hook_on(None)
                context_label = credential_source
            else:
                with _force_non_interactive_exec():
                    _k8s_config.load_config()
                _install_non_interactive_refresh_hook_on(None)
                context_label = "auto-detected"

            from kubernetes.client.api_client import ApiClient

            api_client = ApiClient()
            api_client.configuration.timeout = 5  # type: ignore[attr-defined]
            api = _k8s_client.CoreV1Api(api_client=api_client)
            resources = api.get_api_resources()
            endpoint = api_client.configuration.host  # type: ignore[attr-defined]

            return {
                "success": True,
                "context": context_label,
                "endpoint": endpoint,
                "api_groups": len(resources.resources) if resources else 0,
            }
        except ApiException as exc:
            return {
                "success": False,
                "error": (f"Apiserver rejected the probe ({exc.status}): {exc.reason}"),
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    @staticmethod
    def get_credential_requirements() -> dict:
        """Document the Kubernetes credential parameters operators may set."""
        return {
            "kubeconfig_path": {
                "required": False,
                "description": "Path to a kubeconfig file (defaults to KUBECONFIG / ~/.kube/config)",
            },
            "context": {
                "required": False,
                "description": "Kubeconfig context name (defaults to current-context)",
            },
            "namespace": {
                "required": False,
                "description": "Target namespace (defaults to in-cluster SA namespace or 'default')",
            },
        }

    @staticmethod
    def get_operational_requirements() -> dict:
        """Document operational parameters the init flow may prompt for."""
        return {
            "namespace": {
                "required": False,
                "description": (
                    "Target namespace for managed pods "
                    "(defaults to in-cluster SA namespace or 'default')"
                ),
            },
        }

    @staticmethod
    def get_ui_column_schema() -> list:
        """Return k8s-specific UI column descriptors for machines, requests, and templates."""
        from orb.application.dto.system import UIColumnDescriptor

        return [
            # ------------------------------------------------------------------
            # machines — pod/workload-level columns
            # ------------------------------------------------------------------
            UIColumnDescriptor(
                key="k8s_namespace",
                path="provider_data.namespace",
                label="Namespace",
                kind="badge",
                resource_type="machines",
                provider="k8s",
                sortable=True,
                default_visible=True,
            ),
            UIColumnDescriptor(
                key="k8s_node_name",
                path="provider_data.node_name",
                label="Node",
                kind="text",
                resource_type="machines",
                provider="k8s",
                sortable=True,
                default_visible=True,
            ),
            UIColumnDescriptor(
                key="k8s_phase",
                path="provider_data.phase",
                label="Phase",
                kind="badge",
                resource_type="machines",
                provider="k8s",
                badge_color_map={
                    "Running": "green",
                    "Pending": "orange",
                    "Succeeded": "teal",
                    "Failed": "red",
                    "Unknown": "gray",
                },
                sortable=True,
                default_visible=True,
            ),
            UIColumnDescriptor(
                key="k8s_restart_count",
                path="provider_data.restart_count",
                label="Restarts",
                kind="count",
                resource_type="machines",
                provider="k8s",
                sortable=True,
                default_visible=False,
            ),
            UIColumnDescriptor(
                key="k8s_capacity_type",
                path="provider_data.node_capacity_type",
                label="Capacity Type",
                kind="badge",
                resource_type="machines",
                provider="k8s",
                badge_color_map={"spot": "orange", "on-demand": "blue", "on_demand": "blue"},
                sortable=True,
                default_visible=False,
            ),
            UIColumnDescriptor(
                key="k8s_workload_kind",
                path="provider_api",
                label="Workload Kind",
                kind="badge",
                resource_type="machines",
                provider="k8s",
                badge_color_map={
                    "Pod": "blue",
                    "Deployment": "purple",
                    "StatefulSet": "teal",
                    "Job": "orange",
                },
                sortable=True,
                default_visible=False,
            ),
            # ------------------------------------------------------------------
            # requests — provider-level request columns
            # ------------------------------------------------------------------
            UIColumnDescriptor(
                key="k8s_request_namespace",
                path="provider_data.namespace",
                label="Namespace",
                kind="badge",
                resource_type="requests",
                provider="k8s",
                sortable=True,
                default_visible=True,
            ),
            UIColumnDescriptor(
                key="k8s_request_provider_api",
                path="provider_data.provider_api",
                label="Workload Kind",
                kind="badge",
                resource_type="requests",
                provider="k8s",
                badge_color_map={
                    "Pod": "blue",
                    "Deployment": "purple",
                    "StatefulSet": "teal",
                    "Job": "orange",
                },
                default_visible=True,
            ),
            # ------------------------------------------------------------------
            # templates — k8s template surface
            # ------------------------------------------------------------------
            UIColumnDescriptor(
                key="k8s_template_provider_api",
                path="provider_api",
                label="Workload Kind",
                kind="badge",
                resource_type="templates",
                provider="k8s",
                badge_color_map={
                    "Pod": "blue",
                    "Deployment": "purple",
                    "StatefulSet": "teal",
                    "Job": "orange",
                },
                default_visible=True,
                sortable=True,
            ),
            UIColumnDescriptor(
                key="k8s_template_namespace",
                path="namespace",
                label="Namespace",
                kind="text",
                resource_type="templates",
                provider="k8s",
                default_visible=True,
                sortable=True,
            ),
        ]


__all__ = ["K8sCapabilityService"]
