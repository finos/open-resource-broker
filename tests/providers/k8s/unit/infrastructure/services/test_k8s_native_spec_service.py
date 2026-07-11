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
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.exceptions.k8s_exceptions import K8sError
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


# ---------------------------------------------------------------------------
# native_spec safety holes
# ---------------------------------------------------------------------------


class TestNativeSpecSafetyHoles:
    """Covers three native_spec safety guarantees.

    1. native_spec set but native_spec_enabled=False → warning + typed path.
    2. Both native_spec and pod_spec_override set with native_spec_enabled=True
       → native_spec wins, pod_spec_override ignored with a warning.
    3. Rendered dict missing apiVersion or kind → K8sError raised.
    """

    # ------------------------------------------------------------------
    # Case 1: native_spec set but flag disabled
    # ------------------------------------------------------------------

    def test_warning_logged_when_native_spec_set_but_flag_disabled(self) -> None:
        """When native_spec_enabled=False, native_spec is bypassed with a warning.

        The caller must receive None (typed-builder fallback) and the logger must
        record a warning so the operator is not silently surprised.
        """
        service = _make_service(provider_flag_enabled=False)
        # Replace logger with a mock so we can inspect calls.
        logger_mock = Mock()
        service.native_spec_service.logger = logger_mock

        template = _make_template(native_spec={"apiVersion": "v1", "kind": "Pod"})
        request = _make_request("Pod")

        result = service.process_pod_spec(template, request, namespace="orb-test")

        assert result is None, "Disabled flag must yield None (typed-builder path)"
        logger_mock.warning.assert_called_once()
        warning_message: str = logger_mock.warning.call_args[0][0]
        assert "native_spec_enabled=False" in warning_message
        assert "falling back" in warning_message

    def test_no_warning_when_native_spec_not_set_and_flag_disabled(self) -> None:
        """No warning should be logged when native_spec is not set on the template."""
        service = _make_service(provider_flag_enabled=False)
        logger_mock = Mock()
        service.native_spec_service.logger = logger_mock

        template = _make_template()  # no native_spec
        request = _make_request("Pod")

        result = service.process_pod_spec(template, request, namespace="orb-test")

        assert result is None
        logger_mock.warning.assert_not_called()

    # ------------------------------------------------------------------
    # Case 2: both native_spec and pod_spec_override set
    # ------------------------------------------------------------------

    def test_warning_logged_when_both_native_spec_and_pod_spec_override_set(self) -> None:
        """pod_spec_override is ignored when native_spec takes precedence."""
        service = _make_service()
        logger_mock = Mock()
        service.native_spec_service.logger = logger_mock

        native = {"spec": {"restartPolicy": "Never"}}
        template = K8sTemplate(
            template_id="tpl-conflict",
            image_id="busybox:latest",
            namespace="orb-test",
            native_spec=native,
            pod_spec_override={"metadata": {"labels": {"extra": "label"}}},
        )
        request = _make_request("Pod")

        result = service.process_pod_spec(template, request, namespace="orb-test")

        # The call should succeed; native_spec path is taken.
        assert result is not None
        assert result["kind"] == "Pod"
        # pod_spec_override labels must NOT appear — native_spec won.
        labels = result.get("metadata", {}).get("labels", {})
        assert "extra" not in labels

        # Exactly one warning about pod_spec_override being ignored.
        logger_mock.warning.assert_called_once()
        warning_message: str = logger_mock.warning.call_args[0][0]
        assert "pod_spec_override" in warning_message
        assert "native_spec takes precedence" in warning_message

    def test_no_warning_when_only_pod_spec_override_set_without_native_spec(self) -> None:
        """pod_spec_override alone (no native_spec) must not trigger the conflict warning."""
        service = _make_service()
        logger_mock = Mock()
        service.native_spec_service.logger = logger_mock

        template = K8sTemplate(
            template_id="tpl-override-only",
            image_id="busybox:latest",
            namespace="orb-test",
            pod_spec_override={"metadata": {"labels": {"extra": "label"}}},
        )
        request = _make_request("Pod")

        result = service.process_pod_spec(template, request, namespace="orb-test")

        # Default path taken; no native_spec → no conflict warning.
        assert result is not None
        logger_mock.warning.assert_not_called()

    # ------------------------------------------------------------------
    # Case 3: rendered dict missing apiVersion / kind
    # ------------------------------------------------------------------

    def test_raises_k8s_error_when_api_version_stripped_from_result(self) -> None:
        """A native_spec that overrides apiVersion with an empty string must raise K8sError."""
        service = _make_service()
        # Supply a native_spec that explicitly clears apiVersion so the merged
        # result has an empty string — simulating a misconfigured operator spec.
        native = {"apiVersion": ""}
        template = _make_template(native_spec=native)
        request = _make_request("Pod")

        with pytest.raises(K8sError, match="missing required field"):
            service.process_pod_spec(template, request, namespace="orb-test")

    def test_raises_k8s_error_when_kind_absent_in_native_spec(self) -> None:
        """When the native_spec omits kind and the default template is also missing it,
        a K8sError must be raised so the operator gets a clear message."""
        service = _make_service()

        # Build a native spec that deliberately supplies apiVersion but no kind,
        # and monkeypatch render_default_spec to return a skeleton without kind
        # so the merge result is also missing it.
        original_render = service.render_default_spec

        def _render_no_kind(api_type: str, context: dict[str, Any]) -> dict[str, Any]:
            rendered = original_render(api_type, context)
            rendered.pop("kind", None)
            return rendered

        service.render_default_spec = _render_no_kind  # type: ignore[method-assign]

        native = {"apiVersion": "v1"}  # kind missing from both sides
        template = _make_template(native_spec=native)
        request = _make_request("Pod")

        with pytest.raises(K8sError, match="missing required field"):
            service.process_pod_spec(template, request, namespace="orb-test")

    def test_no_error_when_api_version_and_kind_present(self) -> None:
        """Happy path: a well-formed native_spec must not raise."""
        service = _make_service()
        native = {"spec": {"containers": [{"name": "orb", "image": "busybox:latest"}]}}
        template = _make_template(native_spec=native)
        request = _make_request("Pod")

        result = service.process_pod_spec(template, request, namespace="orb-test")

        assert result is not None
        assert result.get("apiVersion") == "v1"
        assert result.get("kind") == "Pod"
