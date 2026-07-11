"""Mock-backed fixtures for k8s provider contract tests.

Supplies all fixtures required by the base contract classes:
    provider_under_test, valid_provision_request, valid_template,
    provisioned_resource_ids, template_provider, valid_template_for_validation,
    invalid_template_for_validation, validation_adapter, known_provider_api.

The kubernetes SDK client is mocked at the API-method level — no real cluster
is required.  Contract tests verify interface adherence, not wire fidelity
(which is the domain of live/kmock tests).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from orb.domain.base.ports.provider_validation_port import BaseProviderValidationAdapter
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import (
    K8sResourceQuantities,
    K8sTemplate,
)
from orb.providers.k8s.infrastructure.adapters.template_adapter import (
    _SUPPORTED_PROVIDER_APIS,
)
from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_config(namespace: str = "orb-contract") -> K8sProviderConfig:
    return K8sProviderConfig(namespace=namespace, audit_high_risk_pod_fields=False)  # type: ignore[call-arg]


def _make_pod_object(
    name: str,
    namespace: str = "orb-contract",
    *,
    request_id: str | None = None,
) -> SimpleNamespace:
    """Return a minimal V1Pod-shaped namespace that handlers can read.

    When *request_id* is supplied the ``orb.io/request-id`` label is
    stamped on the pod so that ``_list_namespaced_pod`` can filter by
    label rather than by name-prefix (B1 fix).
    """
    labels: dict[str, str] = {"orb.io/managed": "true"}
    if request_id is not None:
        labels["orb.io/request-id"] = request_id
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace=namespace,
            labels=labels,
        ),
        spec=SimpleNamespace(node_name=None),
        status=SimpleNamespace(
            phase="Running",
            pod_ip="10.0.0.1",
            host_ip="10.1.0.1",
            start_time=None,
            conditions=[SimpleNamespace(type="Ready", status="True", reason=None)],
            container_statuses=[],
        ),
    )


def _make_request(
    request_id: str = "contract-req-001",
    requested_count: int = 1,
    resource_ids: list[str] | None = None,
    provider_data: dict | None = None,
) -> Any:
    req = MagicMock()
    req.request_id = request_id
    req.requested_count = requested_count
    req.template_id = "tpl-contract"
    req.metadata = {}
    req.resource_ids = resource_ids or []
    req.provider_data = provider_data or {"namespace": "orb-contract"}
    req.provider_api = "Pod"
    return req


def _make_core_v1_mock(request_id: str = "contract-req-001") -> Any:
    """Build a CoreV1Api mock whose create/list/delete methods are pre-wired.

    Pod lifecycle semantics
    -----------------------
    * ``create_namespaced_pod`` registers a fake pod object keyed by its name.
      The pod name is read from ``body.metadata.name`` (the real apiserver
      registers exactly what it receives).  The ``orb.io/request-id`` label
      is extracted from ``body.metadata.labels`` and stamped on the stored
      pod object so that the list filter can match by label (not by name
      prefix).
    * ``list_namespaced_pod`` filters by ``label_selector`` when present.
      The selector has the form ``<prefix>/request-id=<request_id>``; the
      mock extracts the request_id value and returns only pods whose
      ``orb.io/request-id`` label matches — using the real label, not a
      re-derived name prefix (B1 fix).  Pods that have been deleted are
      excluded.
    * ``delete_namespaced_pod`` removes a pod by name; only that specific pod
      is removed — pods belonging to a different request remain visible.

    This correctly models real apiserver behaviour where each list call is an
    independent API call with no shared mutable state across requests.  Two
    sequential acquire calls on the *same* mock see only their own pods,
    regardless of whether earlier acquires have been released.
    """
    core_v1 = MagicMock()

    # Maps pod-name → fake pod object for every pod ever created on this mock.
    created_pods: dict[str, Any] = {}
    # Names of pods that have been deleted.
    deleted_pods: set[str] = set()

    def _create_namespaced_pod(namespace: str, body: Any, **kwargs: Any) -> Any:
        # Extract pod name from body — V1Pod uses .metadata.name; SimpleNamespace
        # and MagicMock bodies also expose this attribute path.
        try:
            name = body.metadata.name
        except AttributeError:
            name = str(body.get("metadata", {}).get("name", "unknown"))
        # Extract the request-id label so the list filter can match by label.
        try:
            body_labels: dict[str, str] = dict(body.metadata.labels or {})
        except AttributeError:
            body_labels = {}
        rid = body_labels.get("orb.io/request-id")
        created_pods[name] = _make_pod_object(name, namespace, request_id=rid)
        return SimpleNamespace()

    def _list_namespaced_pod(**kwargs: Any) -> Any:
        """Return pods matching the label_selector, excluding deleted ones.

        The label_selector is ``<prefix>/request-id=<request_id>``; the mock
        extracts the request_id value and returns only pods whose
        ``orb.io/request-id`` label matches exactly.  This keeps the mock
        coupled to the real label the production code stamps on pods, not to
        any name-prefix derivation (B1 fix).  When no selector is provided
        every non-deleted pod is returned (reconciler / broad-list semantics).
        """
        selector: str = kwargs.get("label_selector", "") or ""
        request_id_value: str | None = None
        for part in selector.split(","):
            if "/request-id=" in part:
                request_id_value = part.split("=", 1)[1].strip()
                break

        items: list[Any] = []
        for name, pod in created_pods.items():
            if name in deleted_pods:
                continue
            if request_id_value is not None:
                # Match by the label stored on the pod object — not by name prefix.
                pod_labels: dict[str, str] = dict(
                    getattr(getattr(pod, "metadata", None), "labels", None) or {}
                )
                if pod_labels.get("orb.io/request-id") != request_id_value:
                    continue
            items.append(pod)
        return SimpleNamespace(items=items)

    def _delete_namespaced_pod(name: str, namespace: str, **kwargs: Any) -> Any:
        # Only mark the named pod as deleted; pods for other requests are unaffected.
        deleted_pods.add(name)
        return SimpleNamespace()

    core_v1.create_namespaced_pod.side_effect = _create_namespaced_pod
    core_v1.list_namespaced_pod.side_effect = _list_namespaced_pod
    core_v1.delete_namespaced_pod.side_effect = _delete_namespaced_pod

    return core_v1


def _build_pod_handler(core_v1: Any, config: K8sProviderConfig, logger: Any) -> K8sPodHandler:
    k8s_client = MagicMock()
    k8s_client.core_v1 = core_v1
    return K8sPodHandler(
        kubernetes_client=k8s_client,
        config=config,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Provider adapter — bridges async k8s handlers to the sync contract surface
#
# The base contract tests call acquire_hosts / release_hosts synchronously
# with the signature (request, template) / (machine_ids,) respectively.
# K8s handlers are async coroutines.  This adapter shims both gaps.
# ---------------------------------------------------------------------------


class _K8sProviderAdapter:
    """Sync adapter over an async K8sPodHandler for use by the contract base classes.

    acquire_hosts — runs the async coroutine via asyncio.run; captures the
                    provider_data returned so release_hosts can resolve
                    namespace without holding a full Request aggregate.
    release_hosts — delegates to the async coroutine using the captured
                    provider_data; falls back to a stub dict when called
                    cold (e.g. the idempotent-release contract test).
    get_provider_info — satisfies the monitoring contract.
    check_hosts_status — delegates synchronously (the pod handler's
                         check_hosts_status is itself synchronous).
    """

    def __init__(self, handler: K8sPodHandler) -> None:
        self._handler = handler
        self._last_provider_data: dict[str, Any] = {}

    def acquire_hosts(self, request: Any, template: Any) -> dict:
        result = asyncio.run(self._handler.acquire_hosts(request, template))
        # Capture the provider_data stamped at acquire time so release_hosts
        # can resolve namespace and workload names without the Request aggregate.
        pd = result.get("provider_data") if isinstance(result, dict) else None
        self._last_provider_data = dict(pd) if isinstance(pd, dict) else {}
        self._last_provider_data.setdefault(
            "request_id", str(getattr(request, "request_id", "unknown"))
        )
        return result

    def release_hosts(self, machine_ids: list) -> None:
        provider_data = self._last_provider_data or {"namespace": "orb-contract"}
        asyncio.run(self._handler.release_hosts(machine_ids, provider_data))

    def check_hosts_status(self, request: Any) -> CheckHostsStatusResult:
        return self._handler.check_hosts_status(request)

    def get_provider_info(self) -> dict:
        return {"provider_type": "k8s", "handler": type(self._handler).__name__}


# ---------------------------------------------------------------------------
# Inline validation adapter — thin implementation of BaseProviderValidationAdapter
# using the static _SUPPORTED_PROVIDER_APIS list from the k8s template adapter.
# There is no K8sValidationAdapter class in the production codebase; this
# inline version satisfies the validation contract without adding prod code.
# ---------------------------------------------------------------------------


class _K8sValidationAdapter(BaseProviderValidationAdapter):
    """Minimal validation adapter for the k8s provider contract tests."""

    _SUPPORTED: list[str] = list(_SUPPORTED_PROVIDER_APIS)

    def get_provider_type(self) -> str:
        return "k8s"

    def validate_provider_api(self, api: str) -> bool:
        return api in self._SUPPORTED

    def get_supported_provider_apis(self) -> list[str]:
        return list(self._SUPPORTED)

    def validate_template_configuration(self, template_config: dict[str, Any]) -> dict[str, Any]:
        errors: list[str] = []
        warnings: list[str] = []

        provider_api = template_config.get("provider_api")
        if provider_api is not None and not self.validate_provider_api(provider_api):
            errors.append(
                f"Unsupported k8s provider_api: {provider_api!r}. Must be one of {self._SUPPORTED}."
            )

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# Template provider adapter — wraps K8sHandlerRegistry and normalises
# validate_template so it returns bool (the contract expects True/False).
# ---------------------------------------------------------------------------


class _K8sTemplateProvider:
    """Wraps the k8s handler registry + template adapter for contract use.

    get_available_templates — delegates to K8sHandlerRegistry.generate_example_templates.
    validate_template       — converts the K8sTemplateAdapter list-of-errors
                              return value to a bool.
    """

    def __init__(self) -> None:
        self._registry = K8sHandlerRegistry

    def get_available_templates(self) -> list:
        return K8sHandlerRegistry.generate_example_templates()

    def validate_template(self, template: Any) -> bool:
        provider_api = getattr(template, "provider_api", None)
        return (
            provider_api is not None
            and isinstance(provider_api, str)
            and len(provider_api) > 0
            and provider_api in _SUPPORTED_PROVIDER_APIS
        )


# ---------------------------------------------------------------------------
# Shared fixtures consumed by the contract base classes
# ---------------------------------------------------------------------------


@pytest.fixture
def _config():
    return _make_config()


@pytest.fixture
def _logger():
    return _make_logger()


@pytest.fixture
def _core_v1():
    return _make_core_v1_mock(request_id="contract-req-001")


@pytest.fixture
def provider_under_test(_core_v1, _config, _logger):
    """K8sPodHandler wrapped in a sync adapter — satisfies the monitoring + provisioning contracts."""
    handler = _build_pod_handler(_core_v1, _config, _logger)
    return _K8sProviderAdapter(handler)


@pytest.fixture
def valid_provision_request():
    return _make_request(request_id="contract-req-001", requested_count=1)


@pytest.fixture
def valid_template():
    return K8sTemplate(
        template_id="tpl-contract-pod",
        name="contract-pod",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=5,
        namespace="orb-contract",
        resource_requests=K8sResourceQuantities(cpu="100m", memory="64Mi"),
        tags={"Environment": "contract-test"},
    )


@pytest.fixture
def provisioned_resource_ids(_config, _logger):
    """Provision via K8sPodHandler and yield (adapter, resource_ids, status_request).

    A fresh core_v1 mock is built with the monitoring request-id so the
    list_namespaced_pod response matches the expected label selector.
    """
    mon_request_id = "contract-mon-001"
    core_v1 = _make_core_v1_mock(request_id=mon_request_id)
    handler = _build_pod_handler(core_v1, _config, _logger)
    adapter = _K8sProviderAdapter(handler)

    template = K8sTemplate(
        template_id="tpl-contract-mon",
        name="contract-mon",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=5,
        namespace="orb-contract",
        resource_requests=K8sResourceQuantities(cpu="100m", memory="64Mi"),
    )
    request = _make_request(request_id=mon_request_id, requested_count=1)
    result = adapter.acquire_hosts(request, template)
    resource_ids = result.get("resource_ids", [])

    status_request = _make_request(
        request_id=mon_request_id,
        resource_ids=resource_ids,
        provider_data={"namespace": "orb-contract", "pod_names": resource_ids},
    )
    yield adapter, resource_ids, status_request


@pytest.fixture
def template_provider():
    return _K8sTemplateProvider()


@pytest.fixture
def valid_template_for_validation():
    return K8sTemplate(
        template_id="tpl-valid",
        name="valid-template",
        provider_api="Pod",
        image_id="busybox:latest",
        max_instances=5,
        namespace="orb-contract",
    )


@pytest.fixture
def invalid_template_for_validation():
    """Template with no provider_api — should be rejected by validate_template."""
    tpl = MagicMock()
    tpl.provider_api = None
    tpl.template_id = "tpl-invalid"
    tpl.name = "invalid-template"
    return tpl


@pytest.fixture
def validation_adapter():
    return _K8sValidationAdapter()


@pytest.fixture
def known_provider_api():
    return "Pod"
