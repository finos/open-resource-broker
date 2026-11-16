"""KubeSwiftSandboxHandler against a mocked CustomObjectsApi."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from kubernetes.client.exceptions import ApiException

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.handler_plugins import (
    registered_custom_resources,
    registered_k8s_handlers,
    supported_provider_apis,
)
from orb.providers.k8s.infrastructure.handlers.kubeswift.sandbox_spec import (
    KubeSwiftTemplateError,
)
from orb.providers.k8s.infrastructure.handlers.kubeswift_sandbox_handler import (
    SANDBOX_RESOURCE,
    KubeSwiftSandboxHandler,
    register_kubeswift_sandbox_handler,
)
from orb.providers.k8s.validation.template_validator import K8sTemplateValidator

_POOL = {
    "metadata": {"name": "workers"},
    "spec": {
        "image": "alpine:3.20",
        "cpu": 1,
        "memory": "512Mi",
        "network": {"mode": "restricted"},
    },
}


def _handler(**config: Any) -> tuple[KubeSwiftSandboxHandler, MagicMock]:
    settings: dict[str, Any] = {"namespace": "orb-test", "orb_instance_id": "orb-a"}
    settings.update(config)
    client = MagicMock()
    handler = KubeSwiftSandboxHandler(
        kubernetes_client=client,
        config=K8sProviderConfig(**settings),
        logger=MagicMock(),
        max_retries=0,
    )
    return handler, client.custom_objects_api


def _request(count: int = 2, provider_data: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(
        request_id="req-00000000-0000-0000-0000-000000000001",
        requested_count=count,
        provider_data=provider_data or {"namespace": "orb-test"},
    )


def _template(**kubeswift: Any) -> K8sTemplate:
    return K8sTemplate(
        template_id="ks",
        provider_type="k8s",
        provider_api="KubeSwiftSandbox",
        machine_image="alpine:3.20",
        metadata={"kubeswift": kubeswift},
    )


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------


def test_acquire_creates_one_labelled_sandbox_per_machine() -> None:
    handler, api = _handler()
    result = asyncio.run(handler.acquire_hosts(_request(2), _template(cpu=2)))

    assert api.create_namespaced_custom_object.call_count == 2
    names = []
    for call in api.create_namespaced_custom_object.call_args_list:
        kwargs = call.kwargs
        assert kwargs["group"] == "sandbox.kubeswift.io"
        assert kwargs["version"] == "v1alpha1"
        assert kwargs["plural"] == "swiftsandboxes"
        assert kwargs["namespace"] == "orb-test"
        body = kwargs["body"]
        assert body["kind"] == "SwiftSandbox"
        name = body["metadata"]["name"]
        names.append(name)
        assert body["metadata"]["labels"] == {
            "orb.io/managed": "true",
            "orb.io/request-id": "req-00000000-0000-0000-0000-000000000001",
            "orb.io/machine-id": name,
            "orb.io/provider-api": "KubeSwiftSandbox",
            "orb.io/orb-instance": "orb-a",
        }
        assert body["spec"]["image"] == "alpine:3.20"
        assert body["spec"]["cpu"] == 2
    assert sorted(result["machine_ids"]) == sorted(names)
    assert result["provider_data"]["namespace"] == "orb-test"
    assert result["provider_data"]["failed_sandbox_names"] == []


def test_acquire_without_instance_id_omits_the_label() -> None:
    handler, api = _handler(orb_instance_id=None)
    asyncio.run(handler.acquire_hosts(_request(1), _template()))
    labels = api.create_namespaced_custom_object.call_args.kwargs["body"]["metadata"]["labels"]
    assert "orb.io/orb-instance" not in labels


def test_acquire_from_a_pool_reads_the_pool() -> None:
    handler, api = _handler()
    api.get_namespaced_custom_object.return_value = _POOL
    result = asyncio.run(handler.acquire_hosts(_request(1), _template(poolRef="workers")))
    get = api.get_namespaced_custom_object.call_args.kwargs
    assert (get["plural"], get["name"], get["namespace"]) == (
        "swiftsandboxpools",
        "workers",
        "orb-test",
    )
    spec = api.create_namespaced_custom_object.call_args.kwargs["body"]["spec"]
    assert spec["poolRef"] == {"name": "workers"}
    assert result["provider_data"]["pool"] == "workers"


def test_acquire_with_a_missing_pool_is_a_template_error() -> None:
    handler, api = _handler()
    api.get_namespaced_custom_object.side_effect = ApiException(status=404)
    with pytest.raises(KubeSwiftTemplateError, match="not found"):
        asyncio.run(handler.acquire_hosts(_request(1), _template(poolRef="missing")))
    api.create_namespaced_custom_object.assert_not_called()


def test_acquire_reports_partial_creates() -> None:
    handler, api = _handler()
    api.create_namespaced_custom_object.side_effect = [None, ApiException(status=403)]
    result = asyncio.run(handler.acquire_hosts(_request(2), _template()))
    assert len(result["machine_ids"]) == 1
    assert len(result["provider_data"]["failed_sandbox_names"]) == 1


def test_acquire_raises_when_nothing_was_created() -> None:
    handler, api = _handler()
    api.create_namespaced_custom_object.side_effect = ApiException(status=403)
    with pytest.raises(RuntimeError, match="no SwiftSandbox created"):
        asyncio.run(handler.acquire_hosts(_request(2), _template()))


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_check_hosts_status_lists_by_request_id() -> None:
    handler, api = _handler()
    api.list_namespaced_custom_object.return_value = {
        "items": [
            {
                "metadata": {"name": "a", "namespace": "orb-test", "labels": {}},
                "spec": {"image": "alpine:3.20"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "GuestRunning", "status": "True"}],
                    "network": {
                        "primaryIP": "192.168.99.10",
                        "primaryIPScope": "Pod",
                        "podIP": "10.1.2.3",
                    },
                    "nodeName": "worker-1",
                },
            },
            {"metadata": {"name": "b", "namespace": "orb-test"}, "status": {"phase": "Pending"}},
        ]
    }
    result = handler.check_hosts_status(_request(2))

    kwargs = api.list_namespaced_custom_object.call_args.kwargs
    assert kwargs["label_selector"] == "orb.io/request-id=req-00000000-0000-0000-0000-000000000001"
    assert kwargs["namespace"] == "orb-test"
    by_name = {i["instance_id"]: i for i in result.instances}
    assert by_name["a"]["status"] == "running"
    assert by_name["a"]["private_ip"] is None
    assert by_name["a"]["provider_data"]["pod_ip"] == "10.1.2.3"
    assert by_name["b"]["status"] == "pending"
    assert result.fulfilment.state == "in_progress"


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------


def test_release_deletes_each_sandbox_and_ignores_404() -> None:
    handler, api = _handler()
    api.delete_namespaced_custom_object.side_effect = [None, ApiException(status=404)]
    result = asyncio.run(handler.release_hosts(["a", "b"], {"namespace": "orb-test"}))
    assert sorted(result["deleted"]) == ["a", "b"]
    assert result["failed_deletes"] == []
    names = sorted(c.kwargs["name"] for c in api.delete_namespaced_custom_object.call_args_list)
    assert names == ["a", "b"]


def test_release_reports_a_partial_failure() -> None:
    handler, api = _handler()
    api.delete_namespaced_custom_object.side_effect = [None, ApiException(status=403)]
    result = asyncio.run(handler.release_hosts(["a", "b"], {"namespace": "orb-test"}))
    assert len(result["deleted"]) == 1
    assert len(result["failed_deletes"]) == 1


def test_release_raises_when_every_delete_failed() -> None:
    handler, api = _handler()
    api.delete_namespaced_custom_object.side_effect = ApiException(status=403)
    with pytest.raises(RuntimeError, match="All SwiftSandbox deletes failed"):
        asyncio.run(handler.release_hosts(["a"], {"namespace": "orb-test"}))


def test_release_with_no_machines_is_a_no_op() -> None:
    handler, api = _handler()
    assert asyncio.run(handler.release_hosts([], {})) == {"deleted": [], "failed_deletes": []}
    api.delete_namespaced_custom_object.assert_not_called()


# ---------------------------------------------------------------------------
# registration, validation, examples
# ---------------------------------------------------------------------------


def test_registration_adds_the_api_and_its_custom_resource() -> None:
    register_kubeswift_sandbox_handler()
    register_kubeswift_sandbox_handler()  # idempotent
    assert registered_k8s_handlers()["KubeSwiftSandbox"] is KubeSwiftSandboxHandler
    assert "KubeSwiftSandbox" in supported_provider_apis()
    assert registered_custom_resources() == [SANDBOX_RESOURCE]


def test_validator_applies_the_sandbox_rules() -> None:
    register_kubeswift_sandbox_handler()
    validator = K8sTemplateValidator()
    good = validator.validate(_template(cpu=1))
    assert good.valid, good.errors
    bad = validator.validate(_template(poolRef="w", gpuProfileRef="g"))
    assert "a pooled sandbox takes its GPU from the pool; unset the GPU field" in bad.errors


def test_example_templates_validate() -> None:
    register_kubeswift_sandbox_handler()
    validator = K8sTemplateValidator()
    examples = KubeSwiftSandboxHandler.get_example_templates()
    assert [t.template_id for t in examples] == [
        "kubeswift-sandbox-pooled",
        "kubeswift-sandbox-cold",
    ]
    for template in examples:
        result = validator.validate(template)
        assert result.valid, (template.template_id, result.errors)


def test_validator_reads_metadata_from_a_config_dict() -> None:
    # The CLI passes the template as a config dict; the typed coercion drops
    # metadata and machine_bootstrap, so the rule must read the dict itself.
    register_kubeswift_sandbox_handler()
    result = K8sTemplateValidator().validate(
        {
            "template_id": "ks-bad",
            "provider_api": "KubeSwiftSandbox",
            "machine_image": "alpine:3.20",
            "machine_bootstrap": "#cloud-config",
            "resourceRequests": {"cpu": "1"},
            "metadata": {"kubeswift": {"poolRef": "w", "gpuProfileRef": "g"}},
        }
    )
    assert result.errors == [
        "KubeSwiftSandbox has no cloud-init; unset machine_bootstrap",
        "pod fields do not apply to a SwiftSandbox: resource_requests",
        "a pooled sandbox takes its GPU from the pool; unset the GPU field",
    ]


def test_validator_accepts_a_good_config_dict() -> None:
    register_kubeswift_sandbox_handler()
    result = K8sTemplateValidator().validate(
        {
            "template_id": "ks",
            "provider_api": "KubeSwiftSandbox",
            "machine_image": "alpine:3.20",
            "metadata": {"kubeswift": {"poolRef": "w"}},
        }
    )
    assert result.valid, result.errors
