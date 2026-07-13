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


# ---------------------------------------------------------------------------
# Regression: env, volumes, tolerations survive the native-spec merge path
# ---------------------------------------------------------------------------


class TestEnvVolumesTolerationsMergePath:
    """A request whose spec carries env + volumes + tolerations must produce
    a rendered default spec that CONTAINS all three — the native-spec context
    builder must export them and the default templates must conditionally
    render them.
    """

    def _make_rich_template(self) -> K8sTemplate:
        return K8sTemplate(
            template_id="tpl-rich",
            image_id="busybox:latest",
            namespace="orb-test",
            max_instances=2,
            env=[{"name": "FOO", "value": "bar"}, {"name": "BAZ", "value": "qux"}],
            volume_mounts=[{"name": "data", "mountPath": "/data"}],
            volumes=[{"name": "data", "emptyDir": {}}],
            tolerations=[{"key": "dedicated", "operator": "Equal", "value": "orb"}],
        )

    @pytest.mark.parametrize("api_type", ["pod", "deployment", "statefulset", "job"])
    def test_default_spec_contains_env(self, api_type: str) -> None:
        service = _make_service()
        template = self._make_rich_template()
        request = _make_request(
            api_type.capitalize() if api_type != "statefulset" else "StatefulSet"
        )
        ctx = service._build_k8s_context(template, request, namespace="orb-test")

        assert ctx["has_env"] is True, "context must flag has_env=True"
        assert len(ctx["env"]) == 2
        assert ctx["env"][0]["name"] == "FOO"

        out = service.render_default_spec(api_type, ctx)
        # Locate the container spec — Pod is at spec.containers; others are at
        # spec.template.spec.containers.
        if api_type == "pod":
            containers = out["spec"]["containers"]
        else:
            containers = out["spec"]["template"]["spec"]["containers"]
        assert "env" in containers[0], f"{api_type}: env missing from rendered container spec"
        assert containers[0]["env"] == [
            {"name": "FOO", "value": "bar"},
            {"name": "BAZ", "value": "qux"},
        ]

    @pytest.mark.parametrize("api_type", ["pod", "deployment", "statefulset", "job"])
    def test_default_spec_contains_volumes(self, api_type: str) -> None:
        service = _make_service()
        template = self._make_rich_template()
        request = _make_request(
            api_type.capitalize() if api_type != "statefulset" else "StatefulSet"
        )
        ctx = service._build_k8s_context(template, request, namespace="orb-test")

        assert ctx["has_volumes"] is True
        assert ctx["has_volume_mounts"] is True

        out = service.render_default_spec(api_type, ctx)
        if api_type == "pod":
            pod_spec = out["spec"]
            containers = pod_spec["containers"]
        else:
            pod_spec = out["spec"]["template"]["spec"]
            containers = pod_spec["containers"]
        assert "volumes" in pod_spec, f"{api_type}: volumes missing from pod spec"
        assert pod_spec["volumes"] == [{"name": "data", "emptyDir": {}}]
        assert "volumeMounts" in containers[0], f"{api_type}: volumeMounts missing from container"
        assert containers[0]["volumeMounts"] == [{"name": "data", "mountPath": "/data"}]

    @pytest.mark.parametrize("api_type", ["pod", "deployment", "statefulset", "job"])
    def test_default_spec_contains_tolerations(self, api_type: str) -> None:
        service = _make_service()
        template = self._make_rich_template()
        request = _make_request(
            api_type.capitalize() if api_type != "statefulset" else "StatefulSet"
        )
        ctx = service._build_k8s_context(template, request, namespace="orb-test")

        assert ctx["has_tolerations"] is True

        out = service.render_default_spec(api_type, ctx)
        if api_type == "pod":
            pod_spec = out["spec"]
        else:
            pod_spec = out["spec"]["template"]["spec"]
        assert "tolerations" in pod_spec, f"{api_type}: tolerations missing from pod spec"
        assert pod_spec["tolerations"] == [
            {"key": "dedicated", "operator": "Equal", "value": "orb"}
        ]

    def test_absent_fields_do_not_appear_in_default_spec(self) -> None:
        """When env/volumes/tolerations are not set, the rendered spec must not
        contain those keys — conditional rendering must suppress them."""
        service = _make_service()
        template = _make_template()  # no env/volumes/tolerations
        request = _make_request("Pod")
        ctx = service._build_k8s_context(template, request, namespace="orb-test")

        assert ctx["has_env"] is False
        assert ctx["has_volumes"] is False
        assert ctx["has_volume_mounts"] is False
        assert ctx["has_tolerations"] is False

        out = service.render_default_spec("pod", ctx)
        container = out["spec"]["containers"][0]
        assert "env" not in container, "env must be absent when not set"
        assert "volumeMounts" not in container, "volumeMounts must be absent when not set"
        assert "volumes" not in out["spec"], "volumes must be absent when not set"
        assert "tolerations" not in out["spec"], "tolerations must be absent when not set"


# ---------------------------------------------------------------------------
# native_spec_path — file-based manifest loading
# ---------------------------------------------------------------------------


import os


def _make_template_with_path(*, path: str) -> K8sTemplate:
    return K8sTemplate(
        template_id="tpl-path",
        image_id="nginx:1.27",
        namespace="orb-test",
        max_instances=4,
        native_spec_path=path,
    )


class TestNativeSpecPath:
    """Service loads a YAML/JSON manifest file and merges it like inline native_spec."""

    def _make_yaml_manifest(self, tmp_path: Any, *, filename: str = "manifest.yaml") -> str:
        """Write a minimal Deployment YAML with Jinja vars; return absolute path."""
        content = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: '{{ resource_name }}'\n"
            "spec:\n"
            "  replicas: {{ replicas }}\n"
            "  selector:\n"
            "    matchLabels:\n"
            "      app: test\n"
            "  template:\n"
            "    metadata:\n"
            "      labels:\n"
            "        app: test\n"
            "        orb-request-id: '{{ request_id }}'\n"
            "    spec:\n"
            "      containers:\n"
            "      - name: main\n"
            "        image: '{{ image }}'\n"
        )
        p = tmp_path / filename
        p.write_text(content)
        return str(p)

    def test_yaml_path_renders_and_merges_onto_default(self, tmp_path: Any) -> None:
        """A .yaml native_spec_path is loaded, Jinja-rendered, and merged onto default."""
        service = _make_service()
        yaml_path = self._make_yaml_manifest(tmp_path)
        template = _make_template_with_path(path=yaml_path)
        request = _make_request("Deployment", count=3)

        out = service.process_deployment_spec(template, request, namespace="orb-test")

        assert out is not None
        assert out["apiVersion"] == "apps/v1"
        assert out["kind"] == "Deployment"
        # Jinja substitution in the file must have fired.
        assert out["metadata"]["name"].startswith("orb-")
        # replicas from the default template (our yaml overrides replicas too).
        assert out["spec"]["replicas"] == 3

    def test_json_path_renders_and_merges_onto_default(self, tmp_path: Any) -> None:
        """A .json native_spec_path also works — the JSON parse path is taken."""
        json_path = str(tmp_path / "manifest.json")
        with open(json_path, "w") as f:
            f.write(
                '{"apiVersion": "apps/v1", "kind": "Deployment",'
                ' "spec": {"strategy": {"type": "Recreate"}}}'
            )
        service = _make_service()
        template = _make_template_with_path(path=json_path)
        request = _make_request("Deployment", count=2)

        out = service.process_deployment_spec(template, request, namespace="orb-test")

        assert out is not None
        assert out["kind"] == "Deployment"
        assert out["spec"]["strategy"] == {"type": "Recreate"}
        # Default template fills in replicas.
        assert out["spec"]["replicas"] == 2

    def test_inline_native_spec_wins_over_path_when_both_set(self, tmp_path: Any) -> None:
        """Inline native_spec takes precedence over native_spec_path; a warning is logged."""
        yaml_path = self._make_yaml_manifest(tmp_path)
        service = _make_service()
        logger_mock = Mock()
        service.native_spec_service.logger = logger_mock

        template = K8sTemplate(
            template_id="tpl-both",
            image_id="nginx:1.27",
            namespace="orb-test",
            max_instances=2,
            native_spec={"spec": {"strategy": {"type": "Recreate"}}},
            native_spec_path=yaml_path,
        )
        request = _make_request("Deployment", count=1)

        out = service.process_deployment_spec(template, request, namespace="orb-test")

        # Result should come from inline native_spec.
        assert out is not None
        assert out["spec"]["strategy"] == {"type": "Recreate"}
        # Warning logged about native_spec_path being ignored.
        logger_mock.warning.assert_called()
        warnings = [str(call) for call in logger_mock.warning.call_args_list]
        assert any("native_spec_path" in w and "ignored" in w for w in warnings)

    def test_missing_file_raises_k8s_error(self) -> None:
        """A native_spec_path pointing to a non-existent file raises K8sError."""
        service = _make_service()
        template = _make_template_with_path(path="/tmp/this-file-does-not-exist-orb-test.yaml")
        request = _make_request("Deployment", count=1)

        with pytest.raises(K8sError, match="does not exist"):
            service.process_deployment_spec(template, request, namespace="orb-test")

    def test_path_traversal_outside_base_raises_k8s_error(self, tmp_path: Any) -> None:
        """Path traversal outside native_spec_base_path is rejected with K8sError."""
        base_dir = str(tmp_path / "base")
        os.makedirs(base_dir, exist_ok=True)

        # Create the file outside the base (in the parent) to prove the
        # traversal check fires before the existence check.
        outside_file = tmp_path / "outside.yaml"
        outside_file.write_text("apiVersion: v1\nkind: Pod\n")

        app_service = _make_application_service(enabled=True)
        config_port = _make_config_port()
        k8s_config = K8sProviderConfig(
            namespace="orb-test",
            native_spec_enabled=True,
            native_spec_base_path=base_dir,
        )
        service = K8sNativeSpecService(
            native_spec_service=app_service,
            config_port=config_port,
            k8s_config=k8s_config,
        )

        # Attempt traversal via relative path "../outside.yaml".
        template = _make_template_with_path(path="../outside.yaml")
        request = _make_request("Pod", count=1)

        with pytest.raises(K8sError, match="outside.*native_spec_base_path|Path traversal"):
            service.process_pod_spec(template, request, namespace="orb-test")

    def test_absolute_path_inside_base_is_allowed(self, tmp_path: Any) -> None:
        """An absolute path inside the base directory is accepted."""
        base_dir = str(tmp_path / "base")
        os.makedirs(base_dir, exist_ok=True)
        yaml_path = os.path.join(base_dir, "spec.yaml")
        with open(yaml_path, "w") as f:
            f.write(
                "apiVersion: apps/v1\nkind: Deployment\n"
                "spec:\n  selector:\n    matchLabels:\n      app: ok\n"
            )

        app_service = _make_application_service(enabled=True)
        config_port = _make_config_port()
        k8s_config = K8sProviderConfig(
            namespace="orb-test",
            native_spec_enabled=True,
            native_spec_base_path=base_dir,
        )
        service = K8sNativeSpecService(
            native_spec_service=app_service,
            config_port=config_port,
            k8s_config=k8s_config,
        )

        template = _make_template_with_path(path=yaml_path)
        request = _make_request("Deployment", count=1)
        out = service.process_deployment_spec(template, request, namespace="orb-test")
        assert out is not None
        assert out["kind"] == "Deployment"

    def test_disabled_flag_short_circuits_path(self, tmp_path: Any) -> None:
        """When native_spec_enabled=False, native_spec_path is bypassed (returns None)."""
        service = _make_service(provider_flag_enabled=False)
        yaml_path = self._make_yaml_manifest(tmp_path)
        template = _make_template_with_path(path=yaml_path)
        request = _make_request("Deployment", count=1)

        result = service.process_deployment_spec(template, request, namespace="orb-test")
        assert result is None

    def test_path_missing_api_version_raises_k8s_error(self, tmp_path: Any) -> None:
        """When both the file and the default template yield no apiVersion, K8sError is raised.

        We simulate this by monkeypatching render_default_spec to return a
        skeleton without apiVersion so the merged result is also missing it —
        same technique used in TestNativeSpecSafetyHoles.
        """
        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text("kind: Deployment\nspec: {}\n")
        service = _make_service()

        original_render = service.render_default_spec

        def _render_no_api_version(api_type: str, context: dict[str, Any]) -> dict[str, Any]:
            rendered = original_render(api_type, context)
            rendered.pop("apiVersion", None)
            return rendered

        service.render_default_spec = _render_no_api_version  # type: ignore[method-assign]

        template = _make_template_with_path(path=str(bad_yaml))
        request = _make_request("Deployment", count=1)

        with pytest.raises(K8sError, match="missing required field"):
            service.process_deployment_spec(template, request, namespace="orb-test")


# ---------------------------------------------------------------------------
# label_stamper — controller-kind label injection (Slice 5)
# ---------------------------------------------------------------------------


from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
    stamp_native_workload_body,
)


class TestStampNativeWorkloadBody:
    """stamp_native_workload_body must inject ORB labels in three places for
    Deployment/StatefulSet (metadata, pod-template, and selector.matchLabels).
    Job bodies must NOT receive spec.selector (the Job controller manages it).
    """

    _LABEL_PREFIX = "orb.io"
    _REQUEST_ID = "req-test-123"

    def _fake_request(self) -> Any:
        req = Mock()
        req.request_id = self._REQUEST_ID
        req.template_id = "tpl-test"
        return req

    def _deployment_native_body(self) -> dict[str, Any]:
        return {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {}},
                "template": {
                    "metadata": {"labels": {}},
                    "spec": {"containers": [{"name": "main", "image": "nginx:1.27"}]},
                },
            },
        }

    def _statefulset_native_body(self) -> dict[str, Any]:
        return {
            "apiVersion": "apps/v1",
            "kind": "StatefulSet",
            "metadata": {},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {}},
                "template": {
                    "metadata": {"labels": {}},
                    "spec": {"containers": [{"name": "main", "image": "nginx:1.27"}]},
                },
            },
        }

    def _job_native_body(self) -> dict[str, Any]:
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {},
            "spec": {
                "parallelism": 1,
                "completions": 1,
                "template": {
                    "metadata": {"labels": {}},
                    "spec": {
                        "containers": [{"name": "main", "image": "busybox:1.37"}],
                        "restartPolicy": "Never",
                    },
                },
            },
        }

    def test_deployment_metadata_labels_stamped(self) -> None:
        result = stamp_native_workload_body(
            self._deployment_native_body(),
            workload_name="orb-wl1",
            namespace="ns",
            replicas=2,
            request=self._fake_request(),
            label_prefix=self._LABEL_PREFIX,
        )
        labels = result["metadata"]["labels"]
        assert labels[f"{self._LABEL_PREFIX}/request-id"] == self._REQUEST_ID
        assert labels[f"{self._LABEL_PREFIX}/managed"] == "true"

    def test_deployment_pod_template_labels_stamped(self) -> None:
        result = stamp_native_workload_body(
            self._deployment_native_body(),
            workload_name="orb-wl1",
            namespace="ns",
            replicas=2,
            request=self._fake_request(),
            label_prefix=self._LABEL_PREFIX,
        )
        tpl_labels = result["spec"]["template"]["metadata"]["labels"]
        assert tpl_labels[f"{self._LABEL_PREFIX}/request-id"] == self._REQUEST_ID
        assert tpl_labels[f"{self._LABEL_PREFIX}/managed"] == "true"

    def test_deployment_selector_match_labels_stamped(self) -> None:
        """spec.selector.matchLabels must carry request-id for pod-list lookups."""
        result = stamp_native_workload_body(
            self._deployment_native_body(),
            workload_name="orb-wl1",
            namespace="ns",
            replicas=2,
            request=self._fake_request(),
            label_prefix=self._LABEL_PREFIX,
        )
        match_labels = result["spec"]["selector"]["matchLabels"]
        assert match_labels[f"{self._LABEL_PREFIX}/request-id"] == self._REQUEST_ID

    def test_statefulset_selector_match_labels_stamped(self) -> None:
        result = stamp_native_workload_body(
            self._statefulset_native_body(),
            workload_name="orb-sts1",
            namespace="ns",
            replicas=3,
            request=self._fake_request(),
            label_prefix=self._LABEL_PREFIX,
        )
        match_labels = result["spec"]["selector"]["matchLabels"]
        assert match_labels[f"{self._LABEL_PREFIX}/request-id"] == self._REQUEST_ID
        assert result["spec"]["replicas"] == 3

    def test_job_selector_not_stamped(self) -> None:
        """Job bodies must NOT receive spec.selector — the Job controller manages it."""
        result = stamp_native_workload_body(
            self._job_native_body(),
            workload_name="orb-job1",
            namespace="ns",
            replicas=2,
            request=self._fake_request(),
            label_prefix=self._LABEL_PREFIX,
        )
        # Job uses parallelism/completions, NOT replicas.
        assert result["spec"]["parallelism"] == 2
        assert result["spec"]["completions"] == 2
        assert "replicas" not in result["spec"]
        # No spec.selector for Jobs.
        assert "selector" not in result["spec"]

    def test_deployment_replicas_stamped(self) -> None:
        result = stamp_native_workload_body(
            self._deployment_native_body(),
            workload_name="orb-wl2",
            namespace="ns",
            replicas=5,
            request=self._fake_request(),
            label_prefix=self._LABEL_PREFIX,
        )
        assert result["spec"]["replicas"] == 5

    def test_original_body_not_mutated(self) -> None:
        body = self._deployment_native_body()
        stamp_native_workload_body(
            body,
            workload_name="orb-wl3",
            namespace="ns",
            replicas=1,
            request=self._fake_request(),
            label_prefix=self._LABEL_PREFIX,
        )
        # Original must be untouched.
        assert body["metadata"] == {}
        assert body["spec"]["replicas"] == 1

    def test_selector_created_when_absent(self) -> None:
        """Deployment without spec.selector still gets selector.matchLabels stamped."""
        body: dict[str, Any] = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {},
            "spec": {
                "replicas": 1,
                "template": {"metadata": {"labels": {}}, "spec": {"containers": []}},
            },
        }
        result = stamp_native_workload_body(
            body,
            workload_name="orb-wl4",
            namespace="ns",
            replicas=1,
            request=self._fake_request(),
            label_prefix=self._LABEL_PREFIX,
        )
        assert "selector" in result["spec"]
        assert (
            result["spec"]["selector"]["matchLabels"][f"{self._LABEL_PREFIX}/request-id"]
            == self._REQUEST_ID
        )
