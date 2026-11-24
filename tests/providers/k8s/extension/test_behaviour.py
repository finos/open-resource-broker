"""``fakecr`` works end to end with no pod, apps or batch API available."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.operation_outcome import Accepted, Completed
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from tests.providers.k8s.extension.fake_client import FakeK8sClient
from tests.providers.k8s.extension.fakecr_provider import (
    GROUP,
    LABEL_PREFIX,
    FakeCrConfig,
    FakeCrHandler,
    FakeCrStrategy,
    FakeCrTemplate,
)

pytestmark = pytest.mark.unit

GC_ON = {"orphan_gc_enabled": True, "auto_cleanup_orphans": True, "orb_instance_id": "orb-a"}


def _strategy(client: FakeK8sClient, cls: type[FakeCrStrategy] = FakeCrStrategy, **config: Any):
    strategy = cls(
        config=FakeCrConfig(namespace="ns", **config),
        logger=MagicMock(),
        kubernetes_client=client,  # type: ignore[arg-type]
    )
    assert strategy.initialize()
    return strategy


def _request(request_id: str = "11111111-aaaa", count: int = 2, **extra: Any) -> Any:
    template = FakeCrTemplate(template_id="w1", provider_api="Widget", machine_image="widget:1")
    values: dict[str, Any] = {
        "request_id": request_id,
        "requested_count": count,
        "template_id": "w1",
        "provider_api": "Widget",
        "metadata": {"template": template},
        "provider_data": {},
        "request_type": SimpleNamespace(value="acquire"),
    }
    values.update(extra)
    return SimpleNamespace(**values)


def _return_request(provider_data: dict[str, Any]) -> Any:
    return _request(
        request_id="22222222-bbbb",
        request_type=SimpleNamespace(value="return"),
        provider_data=provider_data,
    )


@pytest.mark.asyncio
async def test_acquire_status_return_cancel_use_only_custom_objects() -> None:
    client = FakeK8sClient()
    strategy = _strategy(client)

    accepted = await strategy.acquire(_request())
    assert isinstance(accepted, Accepted)
    names = list(accepted.pending_resource_ids)
    assert len(names) == 2
    assert accepted.metadata["provider_data"]["pod_names"] == names

    status = await strategy.get_status(names, _request(provider_data={"namespace": "ns"}))
    assert {i["instance_id"] for i in status.metadata["instances"]} == set(names)  # type: ignore[union-attr]

    returned = await strategy.return_machines(
        names, _return_request({"pod_names": names, "request_id": "11111111-aaaa"})
    )
    assert isinstance(returned, Accepted)
    assert client.custom_objects_api.objects == {}

    cancelled_client = FakeK8sClient()
    cancelling = _strategy(cancelled_client)
    await cancelling.acquire(_request(request_id="33333333-cccc"))
    result = await cancelling.cancel_resource("33333333-cccc")
    assert result.status == "success"
    assert all(entry.startswith("Widget/") for entry in result.deleted)
    assert cancelled_client.custom_objects_api.objects == {}

    assert client.forbidden_calls() == []
    assert cancelled_client.forbidden_calls() == []


@pytest.mark.asyncio
async def test_objects_carry_the_types_own_labels() -> None:
    client = FakeK8sClient()
    await _strategy(client, orb_instance_id="orb-a").acquire(_request(count=1))
    (obj,) = client.custom_objects_api.objects.values()
    assert obj["apiVersion"].startswith(GROUP)
    assert set(obj["metadata"]["labels"]) == {
        f"{LABEL_PREFIX}/managed",
        f"{LABEL_PREFIX}/request-id",
        f"{LABEL_PREFIX}/orb-instance",
    }
    assert not any(key.startswith("orb.io/") for key in obj["metadata"]["labels"])


@pytest.mark.asyncio
async def test_status_poll_after_return_synthesises_terminated_for_confirmed_names() -> None:
    strategy = _strategy(FakeK8sClient())
    names = ["widget-gone-0", "widget-gone-1"]
    request = _return_request({"pod_names": ["widget-gone-0"], "request_id": "11111111-aaaa"})
    outcome = await strategy.get_status(names, request, machine_coordinates={})
    by_id = {i["instance_id"]: i for i in outcome.metadata["instances"]}  # type: ignore[union-attr]
    assert by_id["widget-gone-0"]["status"] == "terminated"
    assert by_id["widget-gone-0"]["instance_type"] == "fakecr/Widget"
    assert by_id["widget-gone-1"]["status"] == "unknown"


@pytest.mark.asyncio
async def test_return_without_recorded_names_trusts_the_submitted_ids() -> None:
    strategy = _strategy(FakeK8sClient())
    outcome = await strategy.get_status(["widget-x"], _return_request({}), machine_coordinates={})
    assert isinstance(outcome, Completed)
    assert outcome.metadata["instances"][0]["status"] == "terminated"


@pytest.mark.asyncio
async def test_non_return_status_synthesises_for_confirmed_and_unconfirmed_names() -> None:
    strategy = _strategy(FakeK8sClient())
    request = _request(provider_data={"pod_names": ["widget-a"]})
    outcome = await strategy.get_status(["widget-a", "widget-b"], request)
    by_id = {i["instance_id"]: i["status"] for i in outcome.metadata["instances"]}  # type: ignore[union-attr]
    assert by_id == {"widget-a": "terminated", "widget-b": "unknown"}


def test_lifecycle_never_touches_pods() -> None:
    client = FakeK8sClient()
    strategy = _strategy(client)
    strategy.cleanup()
    assert strategy._initialized is False
    assert client.forbidden_calls() == []


@pytest.mark.asyncio
async def test_no_daemon_service_starts_and_the_unrecorded_delete_flag_is_off() -> None:
    client = FakeK8sClient()
    strategy = _strategy(client, **GC_ON)
    await strategy.start_daemon_services()
    assert strategy._orphan_gc is None
    assert strategy._startup_reconciler is None
    assert strategy._watch_manager is None
    assert strategy.get_capabilities().features["deletes_pods_without_request_record"] is False
    assert client.calls == []


def test_unrecorded_delete_flag_follows_the_daemon_set_not_the_kinds() -> None:
    class WithGc(FakeCrStrategy):
        _DAEMON_SERVICES = frozenset({"orphan_gc"})

    features = _strategy(FakeK8sClient(), WithGc, **GC_ON).get_capabilities().features
    assert features["deletes_pods_without_request_record"] is True
    assert features["watch_supported"] is False


def test_health_uses_discovery_only_and_the_types_check_name() -> None:
    client = FakeK8sClient()
    strategy = _strategy(client)
    with patch("kubernetes.client.VersionApi") as version_api:
        version_api.return_value.get_code.side_effect = lambda **_: (
            client.calls.append("version.get_code"),
            SimpleNamespace(git_version="v1.30.0"),
        )[1]
        health = strategy.check_health()
    assert health.is_healthy is True
    assert client.calls == ["core_v1.get_api_resources", "version.get_code"]
    assert client.forbidden_calls() == []
    registered = MagicMock()
    strategy._health_check_service.register_health_checks(
        registered,
        client,  # type: ignore[arg-type]
        check_name=strategy._HEALTH_CHECK_NAME,
    )
    assert registered.register_check.call_args.args[0] == "fakecr_api"


def test_capabilities_exclude_start_and_stop() -> None:
    capabilities = _strategy(FakeK8sClient()).get_capabilities()
    assert capabilities.provider_type == "fakecr"
    assert capabilities.supported_apis == ["Widget"]
    assert ProviderOperationType.START_INSTANCES not in capabilities.supported_operations
    assert ProviderOperationType.STOP_INSTANCES not in capabilities.supported_operations


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation_type",
    [ProviderOperationType.START_INSTANCES, ProviderOperationType.STOP_INSTANCES],
)
async def test_start_and_stop_are_unsupported(operation_type: ProviderOperationType) -> None:
    client = FakeK8sClient()
    result = await _strategy(client).execute_operation(
        ProviderOperation(operation_type=operation_type, parameters={})
    )
    assert result.success is False
    assert result.error_code == "UNSUPPORTED_OPERATION"
    assert client.calls == []


@pytest.mark.asyncio
async def test_results_carry_the_provider_type() -> None:
    result = await _strategy(FakeK8sClient()).execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={},
            context={"dry_run": True},
        )
    )
    assert result.metadata["provider"] == "fakecr"


def _pending_pod() -> dict[str, Any]:
    return {
        "instance_id": "widget-old",
        "name": "widget-old",
        "status": "pending",
        "launch_time": "2020-01-01T00:00:00Z",
        "provider_data": {"namespace": "ns"},
    }


async def _timed_out_rewrite(handler_class: type[FakeCrHandler], client: FakeK8sClient) -> Any:
    handler = handler_class(
        kubernetes_client=client,  # type: ignore[arg-type]
        config=FakeCrConfig(namespace="ns"),  # type: ignore[call-arg]
        logger=MagicMock(),
    )
    rewritten = handler.apply_pod_timeouts([_pending_pod()])
    await asyncio.sleep(0.01)  # let a scheduled deletion task run
    return rewritten


@pytest.mark.asyncio
async def test_pending_timeout_never_deletes_a_pod() -> None:
    client = FakeK8sClient()
    rewritten = await _timed_out_rewrite(FakeCrHandler, client)
    assert rewritten[0]["status"] == "terminated"  # the timeout did fire
    assert client.forbidden_calls() == []


@pytest.mark.asyncio
async def test_pending_timeout_would_delete_a_pod_if_the_handler_allowed_it() -> None:
    class Allowing(FakeCrHandler):
        timeout_delete_enabled = True

    client = FakeK8sClient()
    await _timed_out_rewrite(Allowing, client)
    assert "forbidden:core_v1.delete_namespaced_pod" in client.forbidden_calls()


@pytest.mark.asyncio
async def test_forbidden_attempts_are_recorded_even_when_the_caller_swallows_them() -> None:
    class Defaults(FakeCrStrategy):
        _CANCEL_KINDS = None  # the built-in kinds list pods, deployments and jobs

    client = FakeK8sClient()
    await _strategy(client, Defaults).cancel_resource("44444444-dddd")
    assert "forbidden:core_v1.list_namespaced_pod" in client.forbidden_calls()
    assert "forbidden:apps_v1.list_namespaced_deployment" in client.forbidden_calls()


def test_naming_and_defaults_come_from_the_type() -> None:
    assert FakeCrStrategy.generate_provider_name({"context": "c"}) == "fakecr_c"
    defaults = FakeCrStrategy.get_defaults_config()
    template_defaults = defaults["provider"]["provider_defaults"]["fakecr"]["template_defaults"]
    assert template_defaults["provider_type"] == "fakecr"


def test_example_templates_come_from_the_types_handler_table() -> None:
    from orb.providers.k8s.adapters.template_example_generator_adapter import (
        KubernetesTemplateExampleGeneratorAdapter,
    )

    examples = KubernetesTemplateExampleGeneratorAdapter(
        strategy_class=FakeCrStrategy
    ).generate_example_templates("fakecr")
    assert [t.template_id for t in examples] == ["fakecr-widget-example"]
    assert (
        KubernetesTemplateExampleGeneratorAdapter(
            strategy_class=FakeCrStrategy
        ).generate_example_templates("fakecr", provider_api="Pod")
        == []
    )


def test_the_fake_client_rejects_everything_but_discovery() -> None:
    client = FakeK8sClient()
    client.core_v1.get_api_resources()
    assert client.forbidden_calls() == []
    for attribute in ("list_namespaced_pod", "delete_namespaced_pod", "read_namespaced_pod"):
        with pytest.raises(AssertionError):
            getattr(client.core_v1, attribute)
    with pytest.raises(AssertionError):
        getattr(client.apps_v1, "list_namespaced_deployment")  # noqa: B009
    with pytest.raises(AssertionError):
        getattr(client.batch_v1, "list_namespaced_job")  # noqa: B009
    assert client.forbidden_calls() == [
        "forbidden:core_v1.list_namespaced_pod",
        "forbidden:core_v1.delete_namespaced_pod",
        "forbidden:core_v1.read_namespaced_pod",
        "forbidden:apps_v1.list_namespaced_deployment",
        "forbidden:batch_v1.list_namespaced_job",
    ]
