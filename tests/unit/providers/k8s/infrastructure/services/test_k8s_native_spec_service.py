"""Unit tests for :class:`K8sNativeSpecService`.

Covers:

* enable-flag plumbing — both layers (provider config + application
  flag) must agree before the escape hatch is reported as enabled.
* :meth:`render_default_spec` produces valid kubernetes API bodies for
  every supported API type when rendered with a representative context.
* :meth:`process_pod_spec` (and the other ``process_*`` variants) honour
  the ``native_spec`` override, deep-merging it onto the default.
* Partial overrides (e.g. ``spec.containers[0].resources``) survive the
  deep-merge: defaults are kept, overrides win on leaf collisions.
* Disabled flag (either layer) short-circuits to ``None`` so callers
  fall back to the typed builder.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import Mock

import pytest

from orb.application.services.native_spec_service import NativeSpecService
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.infrastructure.template.jinja_spec_renderer import JinjaSpecRenderer
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template import K8sTemplate
from orb.providers.k8s.infrastructure.services.k8s_native_spec_service import (
    _SUPPORTED_API_TYPES,
    K8sNativeSpecService,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_request(provider_api: str, *, count: int = 2) -> Request:
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api=provider_api,
        template_id="tpl-1",
        requested_count=count,
        provider_data={"namespace": "orb-test"},
    )


def _make_template(*, native_spec: Any = None) -> K8sTemplate:
    return K8sTemplate(
        template_id="tpl-1",
        image_id="busybox:latest",
        namespace="orb-test",
        max_instances=4,
        resource_requests={"cpu": "100m", "memory": "128Mi"},
        native_spec=native_spec,
    )


def _make_application_service(*, enabled: bool = True) -> NativeSpecService:
    config_port = Mock()
    config_port.get_native_spec_config.return_value = {"enabled": enabled}
    config_port.get_package_info.return_value = {"name": "orb", "version": "test"}
    logger = Mock()
    renderer = JinjaSpecRenderer(logger=logger)
    return NativeSpecService(config_port=config_port, spec_renderer=renderer, logger=logger)


def _make_config_port() -> Mock:
    """Mock the ConfigurationPort used by the provider-specific service."""
    port = Mock()
    port.get_package_info.return_value = {"name": "orb", "version": "test"}
    return port


def _make_service(
    *,
    application_flag_enabled: bool = True,
    provider_flag_enabled: bool = True,
) -> K8sNativeSpecService:
    app_service = _make_application_service(enabled=application_flag_enabled)
    config_port = _make_config_port()
    k8s_config = K8sProviderConfig(namespace="orb-test", native_spec_enabled=provider_flag_enabled)
    return K8sNativeSpecService(
        native_spec_service=app_service,
        config_port=config_port,
        k8s_config=k8s_config,
    )


# ---------------------------------------------------------------------------
# Enable-flag plumbing
# ---------------------------------------------------------------------------


class TestIsNativeSpecEnabled:
    def test_returns_true_when_both_layers_enabled(self) -> None:
        service = _make_service(application_flag_enabled=True, provider_flag_enabled=True)
        assert service.is_native_spec_enabled() is True

    def test_returns_false_when_provider_layer_disabled(self) -> None:
        service = _make_service(application_flag_enabled=True, provider_flag_enabled=False)
        assert service.is_native_spec_enabled() is False

    def test_returns_false_when_application_layer_disabled(self) -> None:
        service = _make_service(application_flag_enabled=False, provider_flag_enabled=True)
        assert service.is_native_spec_enabled() is False

    def test_returns_false_when_both_layers_disabled(self) -> None:
        service = _make_service(application_flag_enabled=False, provider_flag_enabled=False)
        assert service.is_native_spec_enabled() is False


# ---------------------------------------------------------------------------
# Default Jinja templates render to valid kubernetes API bodies
# ---------------------------------------------------------------------------


class TestRenderDefaultSpec:
    @pytest.fixture
    def service(self) -> K8sNativeSpecService:
        return _make_service()

    @pytest.fixture
    def context(self, service: K8sNativeSpecService) -> dict[str, Any]:
        template = _make_template()
        request = _make_request("Pod", count=3)
        return service._build_k8s_context(template, request, namespace="orb-test")

    def test_pod_default_renders_to_valid_pod_dict(
        self, service: K8sNativeSpecService, context: dict[str, Any]
    ) -> None:
        out = service.render_default_spec("pod", context)
        assert out["apiVersion"] == "v1"
        assert out["kind"] == "Pod"
        assert out["metadata"]["namespace"] == "orb-test"
        assert out["spec"]["restartPolicy"] == "Never"
        containers = out["spec"]["containers"]
        assert containers[0]["image"] == "busybox:latest"
        assert containers[0]["resources"]["requests"] == {"cpu": "100m", "memory": "128Mi"}

    def test_deployment_default_renders_to_valid_deployment_dict(
        self, service: K8sNativeSpecService, context: dict[str, Any]
    ) -> None:
        out = service.render_default_spec("deployment", context)
        assert out["apiVersion"] == "apps/v1"
        assert out["kind"] == "Deployment"
        assert out["spec"]["replicas"] == 3
        assert "matchLabels" in out["spec"]["selector"]
        assert out["spec"]["template"]["spec"]["restartPolicy"] == "Always"

    def test_statefulset_default_renders_to_valid_statefulset_dict(
        self, service: K8sNativeSpecService, context: dict[str, Any]
    ) -> None:
        out = service.render_default_spec("statefulset", context)
        assert out["apiVersion"] == "apps/v1"
        assert out["kind"] == "StatefulSet"
        assert out["spec"]["replicas"] == 3
        assert out["spec"]["serviceName"].startswith("orb-")

    def test_job_default_renders_to_valid_job_dict(
        self, service: K8sNativeSpecService, context: dict[str, Any]
    ) -> None:
        out = service.render_default_spec("job", context)
        assert out["apiVersion"] == "batch/v1"
        assert out["kind"] == "Job"
        assert out["spec"]["parallelism"] == 3
        assert out["spec"]["completions"] == 3
        assert out["spec"]["backoffLimit"] == 0
        assert out["spec"]["template"]["spec"]["restartPolicy"] == "Never"

    def test_unknown_api_type_raises_value_error(
        self, service: K8sNativeSpecService, context: dict[str, Any]
    ) -> None:
        with pytest.raises(ValueError, match="Unsupported kubernetes native-spec"):
            service.render_default_spec("daemonset", context)

    def test_supported_api_types_match_directory_layout(self) -> None:
        """The advertised API set is exactly the ones with default.json files."""
        assert _SUPPORTED_API_TYPES == frozenset({"pod", "deployment", "statefulset", "job"})


# ---------------------------------------------------------------------------
# Per-API process_* paths
# ---------------------------------------------------------------------------


class TestProcessPodSpec:
    def test_disabled_returns_none(self) -> None:
        service = _make_service(provider_flag_enabled=False)
        template = _make_template(native_spec={"apiVersion": "v1", "kind": "Pod"})
        request = _make_request("Pod")
        assert service.process_pod_spec(template, request, namespace="orb-test") is None

    def test_enabled_no_native_spec_renders_default(self) -> None:
        service = _make_service()
        template = _make_template()
        request = _make_request("Pod")
        out = service.process_pod_spec(template, request, namespace="orb-test")
        assert out is not None
        assert out["kind"] == "Pod"
        assert out["spec"]["containers"][0]["image"] == "busybox:latest"

    def test_enabled_with_native_spec_renders_and_merges(self) -> None:
        service = _make_service()
        # Operator submits a partial override — only the container
        # resources should override the default; everything else comes
        # from the default Jinja template.
        native = {
            "spec": {"containers": [{"name": "orb", "resources": {"requests": {"cpu": "2"}}}]}
        }
        template = _make_template(native_spec=native)
        request = _make_request("Pod")
        out = service.process_pod_spec(template, request, namespace="orb-test")
        assert out is not None
        # Default fields preserved.
        assert out["apiVersion"] == "v1"
        assert out["kind"] == "Pod"
        assert out["spec"]["restartPolicy"] == "Never"
        # Container survives merge — operator's resources win on leaves.
        containers = out["spec"]["containers"]
        assert containers[0]["name"] == "orb"
        assert containers[0]["resources"]["requests"] == {"cpu": "2"}

    def test_native_spec_jinja_variables_are_rendered(self) -> None:
        service = _make_service()
        # The native spec may itself reference Jinja variables.
        native = {
            "metadata": {
                "labels": {"orb.io/request-id": "{{ request_id }}"},
            }
        }
        template = _make_template(native_spec=native)
        request = _make_request("Pod")
        out = service.process_pod_spec(template, request, namespace="orb-test")
        assert out is not None
        assert out["metadata"]["labels"]["orb.io/request-id"] == str(request.request_id)


class TestProcessDeploymentSpec:
    def test_disabled_returns_none(self) -> None:
        service = _make_service(provider_flag_enabled=False)
        template = _make_template(native_spec={"apiVersion": "apps/v1"})
        request = _make_request("Deployment")
        assert service.process_deployment_spec(template, request, namespace="orb-test") is None

    def test_enabled_no_native_spec_renders_default(self) -> None:
        service = _make_service()
        template = _make_template()
        request = _make_request("Deployment", count=4)
        out = service.process_deployment_spec(template, request, namespace="orb-test")
        assert out is not None
        assert out["kind"] == "Deployment"
        assert out["spec"]["replicas"] == 4

    def test_enabled_with_native_spec_merges_with_default(self) -> None:
        service = _make_service()
        native = {"spec": {"strategy": {"type": "Recreate"}}}
        template = _make_template(native_spec=native)
        request = _make_request("Deployment", count=2)
        out = service.process_deployment_spec(template, request, namespace="orb-test")
        assert out is not None
        # Default fields preserved.
        assert out["spec"]["replicas"] == 2
        assert "selector" in out["spec"]
        # Override merged on top.
        assert out["spec"]["strategy"] == {"type": "Recreate"}


class TestProcessStatefulSetSpec:
    def test_enabled_no_native_spec_renders_default(self) -> None:
        service = _make_service()
        template = _make_template()
        request = _make_request("StatefulSet", count=3)
        out = service.process_statefulset_spec(template, request, namespace="orb-test")
        assert out is not None
        assert out["kind"] == "StatefulSet"
        assert out["spec"]["serviceName"].startswith("orb-")

    def test_enabled_with_native_spec_merges(self) -> None:
        service = _make_service()
        native = {"spec": {"podManagementPolicy": "Parallel"}}
        template = _make_template(native_spec=native)
        request = _make_request("StatefulSet")
        out = service.process_statefulset_spec(template, request, namespace="orb-test")
        assert out is not None
        assert out["spec"]["podManagementPolicy"] == "Parallel"
        assert out["spec"]["replicas"] == 2  # from default


class TestProcessJobSpec:
    def test_enabled_no_native_spec_renders_default(self) -> None:
        service = _make_service()
        template = _make_template()
        request = _make_request("Job", count=5)
        out = service.process_job_spec(template, request, namespace="orb-test")
        assert out is not None
        assert out["kind"] == "Job"
        assert out["spec"]["parallelism"] == 5
        assert out["spec"]["completions"] == 5
        assert out["spec"]["backoffLimit"] == 0

    def test_enabled_with_native_spec_merges(self) -> None:
        service = _make_service()
        native = {"spec": {"ttlSecondsAfterFinished": 60}}
        template = _make_template(native_spec=native)
        request = _make_request("Job")
        out = service.process_job_spec(template, request, namespace="orb-test")
        assert out is not None
        assert out["spec"]["ttlSecondsAfterFinished"] == 60
        # Defaults survive.
        assert out["spec"]["backoffLimit"] == 0


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------


class TestContextBuilding:
    def test_context_carries_image_and_namespace(self) -> None:
        service = _make_service()
        template = _make_template()
        request = _make_request("Pod")
        ctx = service._build_k8s_context(template, request, namespace="ns-x")
        assert ctx["image"] == "busybox:latest"
        assert ctx["namespace"] == "ns-x"

    def test_context_carries_replicas_from_request(self) -> None:
        service = _make_service()
        template = _make_template()
        request = _make_request("Deployment", count=7)
        ctx = service._build_k8s_context(template, request, namespace="ns-x")
        assert ctx["replicas"] == 7
        assert ctx["requested_count"] == 7

    def test_context_includes_label_prefix_from_provider_config(self) -> None:
        service = _make_service()
        template = _make_template()
        request = _make_request("Pod")
        ctx = service._build_k8s_context(template, request, namespace="ns-x")
        assert ctx["label_prefix"] == "orb.io"
        assert ctx["labels"]["orb.io/request-id"] == str(request.request_id)

    def test_context_has_command_flags(self) -> None:
        service = _make_service()
        template = K8sTemplate(
            template_id="tpl",
            image_id="busybox:latest",
            command=["echo", "hi"],
            namespace="orb-test",
        )
        request = _make_request("Pod")
        ctx = service._build_k8s_context(template, request, namespace="orb-test")
        assert ctx["has_command"] is True
        assert ctx["command"] == ["echo", "hi"]
