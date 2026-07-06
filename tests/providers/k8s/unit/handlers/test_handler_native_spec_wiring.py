"""Handler-level native-spec integration tests.

Mirrors the structure of :mod:`test_pod_handler` but focuses on the
native-spec escape hatch wiring at the handler boundary:

* When the native-spec service is ``None`` (default, opt-out path), the
  handler MUST call ``build_pod_spec`` / ``build_deployment_spec`` /
  ``build_statefulset_spec`` / ``build_job_spec``.
* When the native-spec service is wired and the operator supplied a
  ``native_spec`` (or the default Jinja template runs), the handler MUST
  pass the rendered dict straight to the kubernetes SDK and skip the
  typed builders entirely.
* Per-pod identity stamping survives the escape-hatch path so the
  request-id label is always populated.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import MagicMock

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template import K8sTemplate
from orb.providers.k8s.handlers.deployment_handler import K8sDeploymentHandler
from orb.providers.k8s.handlers.job_handler import K8sJobHandler
from orb.providers.k8s.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.handlers.statefulset_handler import K8sStatefulSetHandler

# ---------------------------------------------------------------------------
# Shared fixtures
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


def _make_template() -> K8sTemplate:
    return K8sTemplate(
        template_id="tpl-1",
        image_id="busybox:latest",
        namespace="orb-test",
        max_instances=4,
        resource_requests={"cpu": "100m", "memory": "128Mi"},
    )


def _make_native_pod_body() -> dict[str, Any]:
    """A representative native pod body the operator might supply."""
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "operator-supplied", "namespace": "orb-test"},
        "spec": {
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "orb",
                    "image": "operator-image:1",
                    "resources": {"requests": {"cpu": "2"}},
                }
            ],
        },
    }


def _make_native_workload_body(kind: str) -> dict[str, Any]:
    """A representative native workload body."""
    return {
        "apiVersion": "apps/v1" if kind != "Job" else "batch/v1",
        "kind": kind,
        "metadata": {"name": "operator-supplied", "namespace": "orb-test"},
        "spec": {
            "replicas": 9999,  # to be overwritten by the handler
            "selector": {"matchLabels": {"orb.io/request-id": "ignored"}},
            "template": {
                "metadata": {"labels": {"app": "operator"}},
                "spec": {
                    "containers": [{"name": "orb", "image": "operator:1"}],
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Pod handler
# ---------------------------------------------------------------------------


class TestPodHandlerNativeSpec:
    def _make_handler(self, core_v1: Any, native_spec_service: Any = None) -> K8sPodHandler:
        client = MagicMock()
        client.core_v1 = core_v1
        config = K8sProviderConfig(namespace="orb-test")
        return K8sPodHandler(
            kubernetes_client=client,
            config=config,
            logger=MagicMock(),
            native_spec_service=native_spec_service,
        )

    def test_no_native_spec_service_uses_build_pod_spec(self) -> None:
        core_v1 = MagicMock()
        handler = self._make_handler(core_v1, native_spec_service=None)
        request = _make_request("Pod", count=2)
        template = _make_template()

        asyncio.run(handler.acquire_hosts(request, template))

        # Inspect the body passed to create_namespaced_pod — the typed
        # builder returns a ``V1Pod`` SDK object, not a plain dict.
        assert core_v1.create_namespaced_pod.call_count == 2
        call_kwargs = core_v1.create_namespaced_pod.call_args_list[0].kwargs
        body = call_kwargs["body"]
        assert hasattr(body, "spec"), "build_pod_spec must return a V1Pod"

    def test_with_native_spec_service_passes_rendered_dict(self) -> None:
        core_v1 = MagicMock()
        native_body = _make_native_pod_body()
        native_spec_service = MagicMock()
        native_spec_service.process_pod_spec.return_value = native_body
        handler = self._make_handler(core_v1, native_spec_service=native_spec_service)

        request = _make_request("Pod", count=2)
        template = _make_template()

        asyncio.run(handler.acquire_hosts(request, template))

        # The native body should drive the create calls.
        assert core_v1.create_namespaced_pod.call_count == 2
        first_call_body = core_v1.create_namespaced_pod.call_args_list[0].kwargs["body"]
        # The native body is a dict, NOT a V1Pod.
        assert isinstance(first_call_body, dict)
        # Per-pod stamping preserves the request-id label.
        assert first_call_body["metadata"]["labels"]["orb.io/request-id"] == str(request.request_id)

    def test_with_native_spec_disabled_falls_back_to_builder(self) -> None:
        core_v1 = MagicMock()
        # Service is wired but reports disabled — process_pod_spec returns None.
        native_spec_service = MagicMock()
        native_spec_service.process_pod_spec.return_value = None
        handler = self._make_handler(core_v1, native_spec_service=native_spec_service)

        request = _make_request("Pod", count=2)
        template = _make_template()

        asyncio.run(handler.acquire_hosts(request, template))

        # Body is a V1Pod object (typed builder path), not a dict.
        first_call_body = core_v1.create_namespaced_pod.call_args_list[0].kwargs["body"]
        assert hasattr(first_call_body, "spec")
        assert not isinstance(first_call_body, dict)


# ---------------------------------------------------------------------------
# Deployment handler
# ---------------------------------------------------------------------------


class TestDeploymentHandlerNativeSpec:
    def _make_handler(self, apps_v1: Any, native_spec_service: Any = None) -> K8sDeploymentHandler:
        client = MagicMock()
        client.apps_v1 = apps_v1
        config = K8sProviderConfig(namespace="orb-test")
        return K8sDeploymentHandler(
            kubernetes_client=client,
            config=config,
            logger=MagicMock(),
            native_spec_service=native_spec_service,
        )

    def test_no_native_spec_service_uses_build_deployment_spec(self) -> None:
        apps_v1 = MagicMock()
        handler = self._make_handler(apps_v1, native_spec_service=None)
        request = _make_request("Deployment", count=3)
        template = _make_template()

        asyncio.run(handler.acquire_hosts(request, template))

        apps_v1.create_namespaced_deployment.assert_called_once()
        body = apps_v1.create_namespaced_deployment.call_args.kwargs["body"]
        assert hasattr(body, "spec"), "build_deployment_spec must return a V1Deployment"

    def test_with_native_spec_passes_rendered_dict_and_stamps_identity(self) -> None:
        apps_v1 = MagicMock()
        native_body = _make_native_workload_body("Deployment")
        native_spec_service = MagicMock()
        native_spec_service.process_deployment_spec.return_value = native_body
        handler = self._make_handler(apps_v1, native_spec_service=native_spec_service)

        request = _make_request("Deployment", count=5)
        template = _make_template()
        asyncio.run(handler.acquire_hosts(request, template))

        apps_v1.create_namespaced_deployment.assert_called_once()
        body = apps_v1.create_namespaced_deployment.call_args.kwargs["body"]
        assert isinstance(body, dict)
        # Identity stamped on metadata.
        assert body["metadata"]["labels"]["orb.io/request-id"] == str(request.request_id)
        # Replicas overwritten by the handler.
        assert body["spec"]["replicas"] == 5
        # Pod-template labels also stamped.
        assert body["spec"]["template"]["metadata"]["labels"]["orb.io/request-id"] == str(
            request.request_id
        )


# ---------------------------------------------------------------------------
# StatefulSet handler
# ---------------------------------------------------------------------------


class TestStatefulSetHandlerNativeSpec:
    def _make_handler(self, apps_v1: Any, native_spec_service: Any = None) -> K8sStatefulSetHandler:
        client = MagicMock()
        client.apps_v1 = apps_v1
        config = K8sProviderConfig(namespace="orb-test")
        return K8sStatefulSetHandler(
            kubernetes_client=client,
            config=config,
            logger=MagicMock(),
            native_spec_service=native_spec_service,
        )

    def test_no_native_spec_service_uses_build_statefulset_spec(self) -> None:
        apps_v1 = MagicMock()
        handler = self._make_handler(apps_v1, native_spec_service=None)
        request = _make_request("StatefulSet", count=3)
        template = _make_template()

        asyncio.run(handler.acquire_hosts(request, template))

        apps_v1.create_namespaced_stateful_set.assert_called_once()
        body = apps_v1.create_namespaced_stateful_set.call_args.kwargs["body"]
        assert hasattr(body, "spec")

    def test_with_native_spec_passes_rendered_dict(self) -> None:
        apps_v1 = MagicMock()
        native_body = _make_native_workload_body("StatefulSet")
        native_spec_service = MagicMock()
        native_spec_service.process_statefulset_spec.return_value = native_body
        handler = self._make_handler(apps_v1, native_spec_service=native_spec_service)

        request = _make_request("StatefulSet", count=3)
        template = _make_template()
        asyncio.run(handler.acquire_hosts(request, template))

        body = apps_v1.create_namespaced_stateful_set.call_args.kwargs["body"]
        assert isinstance(body, dict)
        assert body["spec"]["replicas"] == 3


# ---------------------------------------------------------------------------
# Job handler
# ---------------------------------------------------------------------------


class TestJobHandlerNativeSpec:
    def _make_handler(self, batch_v1: Any, native_spec_service: Any = None) -> K8sJobHandler:
        client = MagicMock()
        client.batch_v1 = batch_v1
        config = K8sProviderConfig(namespace="orb-test")
        return K8sJobHandler(
            kubernetes_client=client,
            config=config,
            logger=MagicMock(),
            native_spec_service=native_spec_service,
        )

    def test_no_native_spec_service_uses_build_job_spec(self) -> None:
        batch_v1 = MagicMock()
        handler = self._make_handler(batch_v1, native_spec_service=None)
        request = _make_request("Job", count=2)
        template = _make_template()

        asyncio.run(handler.acquire_hosts(request, template))

        batch_v1.create_namespaced_job.assert_called_once()
        body = batch_v1.create_namespaced_job.call_args.kwargs["body"]
        assert hasattr(body, "spec")

    def test_with_native_spec_passes_dict_and_overwrites_parallelism(self) -> None:
        batch_v1 = MagicMock()
        # Job's native body uses parallelism/completions, not replicas.
        native_body = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {"name": "old-name"},
            "spec": {
                "parallelism": 1,
                "completions": 1,
                "backoffLimit": 0,
                "template": {
                    "metadata": {},
                    "spec": {"containers": [{"name": "orb", "image": "op:1"}]},
                },
            },
        }
        native_spec_service = MagicMock()
        native_spec_service.process_job_spec.return_value = native_body
        handler = self._make_handler(batch_v1, native_spec_service=native_spec_service)

        request = _make_request("Job", count=4)
        template = _make_template()
        asyncio.run(handler.acquire_hosts(request, template))

        body = batch_v1.create_namespaced_job.call_args.kwargs["body"]
        assert isinstance(body, dict)
        assert body["spec"]["parallelism"] == 4
        assert body["spec"]["completions"] == 4
