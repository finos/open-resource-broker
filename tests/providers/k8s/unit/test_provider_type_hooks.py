"""The extension hooks of the Kubernetes provider keep today's values by default.

A provider type built on the Kubernetes provider overrides these hooks on a
``K8sProviderStrategy`` subclass.  Every default is pinned here against the
value the Kubernetes provider used before the hooks existed, and each hook is
shown to take effect when a subclass overrides it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.domain.base.operation_outcome import Accepted
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderOperation,
    ProviderOperationType,
)
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.infrastructure.handlers.deployment_handler import K8sDeploymentHandler
from orb.providers.k8s.infrastructure.handlers.job_handler import K8sJobHandler
from orb.providers.k8s.infrastructure.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.infrastructure.handlers.statefulset_handler import K8sStatefulSetHandler
from orb.providers.k8s.services.capability_service import K8sCapabilityService
from orb.providers.k8s.services.instance_operation_service import (
    CancelKind,
    K8sInstanceOperationService,
)
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

FEATURE = "deletes_pods_without_request_record"
GC_ON = {
    "orphan_gc_enabled": True,
    "auto_cleanup_orphans": True,
    "orb_instance_id": "orb-a",
}


def _config(**kwargs: Any) -> K8sProviderConfig:
    return K8sProviderConfig(namespace="ns", **kwargs)  # type: ignore[call-arg]


def _strategy(cls: type[K8sProviderStrategy] = K8sProviderStrategy, **config: Any) -> Any:
    strategy = cls(config=_config(**config), logger=MagicMock(), kubernetes_client=MagicMock())
    strategy._initialized = True
    return strategy


class TestDefaultsMatchTheKubernetesProvider:
    def test_identity(self) -> None:
        strategy = _strategy()
        assert strategy.provider_type == "k8s"
        assert K8sProviderStrategy._CONFIG_CLASS is K8sProviderConfig
        assert K8sProviderStrategy._TEMPLATE_CLASS is K8sTemplate
        assert K8sTemplate(template_id="t").provider_type == "k8s"
        assert K8sProviderStrategy._HEALTH_CHECK_NAME == "kubernetes_api"

    def test_supported_apis_and_aliases(self) -> None:
        assert _strategy().get_supported_apis() == [
            "Pod",
            "Deployment",
            "StatefulSet",
            "Job",
        ]
        assert K8sProviderStrategy._DEFAULT_API == "Pod"
        assert K8sProviderStrategy._API_ALIASES == {
            "pod": "Pod",
            "deployment": "Deployment",
            "statefulset": "StatefulSet",
            "job": "Job",
        }

    def test_handler_table(self) -> None:
        assert dict(K8sProviderStrategy._HANDLER_CLASSES) == {
            "Pod": K8sPodHandler,
            "Deployment": K8sDeploymentHandler,
            "StatefulSet": K8sStatefulSetHandler,
            "Job": K8sJobHandler,
        }

    def test_daemon_services(self) -> None:
        assert K8sProviderStrategy._DAEMON_SERVICES == frozenset(
            {"reconciler", "watch", "orphan_gc", "node_watcher", "events_watcher"}
        )

    def test_cancel_kinds(self) -> None:
        assert K8sProviderStrategy._CANCEL_KINDS is None
        service = _strategy()._instance_operation_service
        assert service.kind_names == ("Pod", "Deployment", "StatefulSet", "Job")

    def test_naming(self) -> None:
        assert K8sProviderStrategy.generate_provider_name({"context": "my ctx"}) == "k8s_my-ctx"
        assert K8sProviderStrategy.generate_provider_name({}) == "k8s_in-cluster"
        strategy = _strategy()
        assert strategy.parse_provider_name("k8s_prod") == {"context_or_namespace": "prod"}
        assert strategy.parse_provider_name("aws_prod") == {}
        assert strategy.get_provider_name_pattern() == "k8s_{sanitized_context}"

    def test_defaults_file(self) -> None:
        assert K8sProviderStrategy._DEFAULTS_RESOURCE == (
            "orb.providers.k8s.config",
            "k8s_defaults.json",
        )
        defaults = K8sProviderStrategy.get_defaults_config()
        provider_defaults = defaults["provider"]["provider_defaults"]["k8s"]
        assert provider_defaults["template_defaults"] == {
            "machine_image": "busybox:latest",
            "provider_api": "Pod",
        }
        assert sorted(provider_defaults["handlers"]) == ["Deployment", "Job", "Pod", "StatefulSet"]

    def test_capabilities(self) -> None:
        capabilities = _strategy().get_capabilities()
        assert capabilities.provider_type == "k8s"
        assert capabilities.supported_apis == ["Pod", "Deployment", "StatefulSet", "Job"]
        assert capabilities.supported_operations == [
            ProviderOperationType.CREATE_INSTANCES,
            ProviderOperationType.TERMINATE_INSTANCES,
            ProviderOperationType.CANCEL_PROVISIONING,
            ProviderOperationType.GET_INSTANCE_STATUS,
            ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            ProviderOperationType.VALIDATE_TEMPLATE,
            ProviderOperationType.HEALTH_CHECK,
            ProviderOperationType.START_INSTANCES,
            ProviderOperationType.STOP_INSTANCES,
        ]
        assert capabilities.features == {
            "selective_termination": False,
            "selective_termination_by_api": {
                "Pod": True,
                "Deployment": True,
                "StatefulSet": True,
                "Job": False,
            },
            "start_stop_supported": False,
            "start_stop_supported_by_api": {
                "Pod": False,
                "Deployment": True,
                "StatefulSet": True,
                "Job": False,
            },
            "watch_supported": True,
            "namespaces_supported": True,
            FEATURE: False,
        }

    def test_handler_flags(self) -> None:
        assert K8sPodHandler.resource_ids_name_machines is True
        for handler in (K8sDeploymentHandler, K8sStatefulSetHandler, K8sJobHandler):
            assert handler.resource_ids_name_machines is False
        for handler in (K8sPodHandler, K8sDeploymentHandler, K8sStatefulSetHandler, K8sJobHandler):
            assert handler.timeout_delete_enabled is True

    @pytest.mark.asyncio
    async def test_dry_run_labels_the_result_with_the_provider_type(self) -> None:
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={},
            context={"dry_run": True},
        )
        result = await _strategy().execute_operation(operation)
        assert result.metadata["provider"] == "k8s"

    def test_registry_synthetic_instance_type_and_default_api(self) -> None:
        registry = _strategy()._handler_registry
        request = MagicMock(provider_api=None)
        assert registry.resolve_provider_api(request) == "Pod"
        assert registry._provider_type == "k8s"
        assert registry._default_api == "Pod"
        assert registry._template_class is K8sTemplate

    @pytest.mark.asyncio
    @pytest.mark.parametrize("api", ["Pod", "Deployment", "StatefulSet", "Job"])
    async def test_synthetic_instance_types_are_unchanged(self, api: str) -> None:
        from orb.providers.k8s.infrastructure.handlers.base_handler import PodReadResult

        strategy = _strategy()
        handler = MagicMock()
        handler.read_pods_for_return.return_value = PodReadResult()
        strategy._handler_registry.handlers[api] = handler
        request = MagicMock(provider_api=api, request_type=MagicMock(value="return"))
        request.provider_data = {"pod_names": ["gone-1"]}
        outcome = await strategy.get_status(["gone-1"], request, machine_coordinates={})
        (instance,) = outcome.metadata["instances"]  # type: ignore[union-attr]
        assert instance["instance_type"] == f"k8s/{api}"
        assert instance["status"] == "terminated"


class TestCapabilitiesHook:
    def test_flag_is_added_after_the_hook(self) -> None:
        class Bare(K8sCapabilityService):
            def _build_capabilities(self) -> ProviderCapabilities:
                return ProviderCapabilities(
                    provider_type="bare",
                    supported_operations=[ProviderOperationType.CREATE_INSTANCES],
                    features={FEATURE: False, "x": 1},
                )

        class BareStrategy(K8sProviderStrategy):
            _PROVIDER_TYPE = "bare"
            _CAPABILITY_SERVICE_CLASS = Bare

        features = _strategy(BareStrategy, **GC_ON).get_capabilities().features
        assert features == {"x": 1, FEATURE: True}

    def test_flag_is_false_when_the_type_runs_no_orphan_gc(self) -> None:
        class NoGc(K8sProviderStrategy):
            _DAEMON_SERVICES = frozenset({"watch"})

        assert _strategy(NoGc, **GC_ON).get_capabilities().features[FEATURE] is False
        assert _strategy(K8sProviderStrategy, **GC_ON).get_capabilities().features[FEATURE] is True

    def test_provider_type_reaches_capabilities_and_names(self) -> None:
        class Other(K8sProviderStrategy):
            _PROVIDER_TYPE = "other"

        strategy = _strategy(Other)
        assert strategy.get_capabilities().provider_type == "other"
        assert Other.generate_provider_name({"context": "c"}) == "other_c"
        assert strategy.get_provider_name_pattern() == "other_{sanitized_context}"
        assert strategy.parse_provider_name("other_c") == {"context_or_namespace": "c"}

    @pytest.mark.asyncio
    async def test_start_is_refused_when_not_declared(self) -> None:
        class NoStartStop(K8sProviderStrategy):
            class _Caps(K8sCapabilityService):
                def _build_capabilities(self) -> ProviderCapabilities:
                    return ProviderCapabilities(
                        provider_type="k8s",
                        supported_operations=[ProviderOperationType.CREATE_INSTANCES],
                    )

            _CAPABILITY_SERVICE_CLASS = _Caps

        strategy = _strategy(NoStartStop)
        strategy._get_start_stop_service = MagicMock(side_effect=AssertionError("reached"))
        for operation_type in (
            ProviderOperationType.START_INSTANCES,
            ProviderOperationType.STOP_INSTANCES,
        ):
            result = await strategy.execute_operation(
                ProviderOperation(operation_type=operation_type, parameters={})
            )
            assert result.success is False
            assert result.error_code == "UNSUPPORTED_OPERATION"


class TestDaemonServices:
    @pytest.mark.asyncio
    async def test_only_listed_services_start(self) -> None:
        class GcOnly(K8sProviderStrategy):
            _DAEMON_SERVICES = frozenset({"orphan_gc"})

        strategy = _strategy(GcOnly)
        for name in (
            "_run_startup_reconciler",
            "_maybe_start_watch_manager",
            "_maybe_start_orphan_gc",
            "_maybe_start_node_watcher",
            "_maybe_start_events_watcher",
        ):
            setattr(
                strategy, name, AsyncMock() if name == "_run_startup_reconciler" else MagicMock()
            )
        await strategy.start_daemon_services()
        strategy._maybe_start_orphan_gc.assert_called_once()
        strategy._run_startup_reconciler.assert_not_called()
        strategy._maybe_start_watch_manager.assert_not_called()
        strategy._maybe_start_node_watcher.assert_not_called()
        strategy._maybe_start_events_watcher.assert_not_called()

    @pytest.mark.asyncio
    async def test_default_starts_everything(self) -> None:
        strategy = _strategy()
        for name in (
            "_run_startup_reconciler",
            "_maybe_start_watch_manager",
            "_maybe_start_orphan_gc",
            "_maybe_start_node_watcher",
            "_maybe_start_events_watcher",
        ):
            setattr(
                strategy, name, AsyncMock() if name == "_run_startup_reconciler" else MagicMock()
            )
        await strategy.start_daemon_services()
        strategy._run_startup_reconciler.assert_awaited_once()
        for name in (
            "_maybe_start_watch_manager",
            "_maybe_start_orphan_gc",
            "_maybe_start_node_watcher",
            "_maybe_start_events_watcher",
        ):
            getattr(strategy, name).assert_called_once()


class TestCancelKindsTable:
    @pytest.mark.asyncio
    async def test_custom_table_is_listed_and_deleted(self) -> None:
        listed: list[tuple[str, str]] = []
        deleted: list[str] = []

        def list_names(client: Any, ns: str, selector: str) -> list[str]:
            listed.append((ns, selector))
            return ["w1"]

        kinds = [CancelKind("Widget", list_names, lambda client, ns, name: deleted.append(name))]
        service = K8sInstanceOperationService(_config(), MagicMock(), kinds=kinds)
        result = await service.cancel_resource("req-1", MagicMock())
        assert listed == [("ns", "orb.io/request-id=req-1")]
        assert deleted == ["w1"]
        assert result.deleted == ["Widget/w1"]
        assert service.kind_names == ("Widget",)

    @pytest.mark.asyncio
    async def test_strategy_passes_its_table(self) -> None:
        class Other(K8sProviderStrategy):
            _CANCEL_KINDS = (CancelKind("Widget", lambda c, n, s: [], lambda c, n, name: None),)

        assert _strategy(Other)._instance_operation_service.kind_names == ("Widget",)


class TestHandlerRegistryHooks:
    def test_handler_table_comes_from_the_strategy(self) -> None:
        class Other(K8sProviderStrategy):
            _HANDLER_CLASSES = {"Widget": K8sPodHandler}

        registry = _strategy(Other)._handler_registry
        assert list(registry._handler_classes) == ["Widget"]
        assert list(_strategy()._handler_registry._handler_classes) == [
            "Pod",
            "Deployment",
            "StatefulSet",
            "Job",
        ]

    def test_example_generator_reads_the_strategy_table(self) -> None:
        from orb.providers.k8s.adapters.template_example_generator_adapter import (
            KubernetesTemplateExampleGeneratorAdapter,
        )

        class Widget:
            @classmethod
            def get_example_templates(cls) -> list[str]:
                return ["widget-example"]

        class Other(K8sProviderStrategy):
            _HANDLER_CLASSES = {"Widget": Widget}  # type: ignore[dict-item]

        assert KubernetesTemplateExampleGeneratorAdapter(
            strategy_class=Other
        ).generate_example_templates("p") == ["widget-example"]
        names = {
            t.provider_api
            for t in KubernetesTemplateExampleGeneratorAdapter().generate_example_templates("p")
        }
        assert names == {"Pod", "Deployment", "StatefulSet", "Job"}

    def test_template_class_and_provider_type_reach_the_fallback_template(self) -> None:
        class WidgetTemplate(K8sTemplate):
            DEFAULT_PROVIDER_TYPE = "other"

        class Other(K8sProviderStrategy):
            _PROVIDER_TYPE = "other"
            _TEMPLATE_CLASS = WidgetTemplate

        request = MagicMock(
            template_id="t", provider_api=None, requested_count=2, metadata={}, spec=None
        )
        template = _strategy(Other)._build_template_for_request(request)
        assert isinstance(template, WidgetTemplate)
        assert template.provider_type == "other"

    @pytest.mark.asyncio
    async def test_controller_handler_ids_are_not_reconciled_as_machines(self) -> None:
        handler = K8sDeploymentHandler(
            kubernetes_client=MagicMock(), config=_config(), logger=MagicMock()
        )
        handler.check_hosts_status = MagicMock(  # type: ignore[method-assign]
            return_value=MagicMock(instances=[], fulfilment=MagicMock(state="in_progress"))
        )
        strategy = _strategy()
        strategy._handler_registry.handlers["Deployment"] = handler
        request = MagicMock(provider_api="Deployment", request_type=MagicMock(value="acquire"))
        request.provider_data = {}
        outcome = await strategy.get_status(["controller-1"], request)
        assert isinstance(outcome, Accepted)
        assert outcome.metadata["instances"] == []


class TestPendingTimeoutDeletion:
    def test_delete_is_skipped_when_the_handler_turns_it_off(self) -> None:
        class NoDelete(K8sPodHandler):
            timeout_delete_enabled = False

        for handler_class, expected in ((K8sPodHandler, 1), (NoDelete, 0)):
            handler = handler_class(
                kubernetes_client=MagicMock(), config=_config(), logger=MagicMock()
            )
            handler._schedule_timed_out_pod_deletions = MagicMock()  # type: ignore[method-assign]
            handler.apply_pod_timeouts([])
            assert handler._schedule_timed_out_pod_deletions.call_count == expected


class TestTimedOutDeletionTasks:
    @pytest.mark.asyncio
    async def test_deletion_task_is_referenced_until_finished(self, monkeypatch) -> None:
        import asyncio

        gate = asyncio.Event()

        async def _slow_delete(*_a: Any, **_k: Any) -> None:
            await gate.wait()

        monkeypatch.setattr(
            "orb.providers.k8s.reconciliation.timeout_gc.delete_timed_out_pod_async",
            _slow_delete,
        )
        handler = K8sPodHandler(kubernetes_client=MagicMock(), config=_config(), logger=MagicMock())
        timed_out = {"instance_id": "p", "name": "p", "provider_data": {"timed_out": True}}
        handler._schedule_timed_out_pod_deletions([{"instance_id": "p"}], [timed_out])
        assert len(handler.background_tasks) == 1

        gate.set()
        await asyncio.sleep(0.01)
        assert len(handler.background_tasks) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_stops_outstanding_deletions(self, monkeypatch) -> None:
        import asyncio

        async def _never(*_a: Any, **_k: Any) -> None:
            await asyncio.Event().wait()

        monkeypatch.setattr(
            "orb.providers.k8s.reconciliation.timeout_gc.delete_timed_out_pod_async", _never
        )
        handler = K8sPodHandler(kubernetes_client=MagicMock(), config=_config(), logger=MagicMock())
        timed_out = {"instance_id": "p", "name": "p", "provider_data": {"timed_out": True}}
        handler._schedule_timed_out_pod_deletions([{"instance_id": "p"}], [timed_out])
        handler.background_tasks.cancel_all()
        await asyncio.sleep(0.01)
        assert len(handler.background_tasks) == 0


class TestHealthCheckName:
    def test_check_name_is_configurable(self) -> None:
        from orb.providers.k8s.health import register_k8s_health_checks

        health_check = MagicMock()
        register_k8s_health_checks(health_check, MagicMock(), check_name="other_api")
        assert health_check.register_check.call_args.args[0] == "other_api"
        health_check = MagicMock()
        register_k8s_health_checks(health_check, MagicMock())
        assert health_check.register_check.call_args.args[0] == "kubernetes_api"


class TestMetricsOperations:
    def test_custom_object_calls_have_their_own_buckets(self) -> None:
        from orb.providers.k8s.infrastructure.instrumentation.metrics import API_OPERATIONS

        assert {
            "create_namespaced_custom_object",
            "get_namespaced_custom_object",
            "list_namespaced_custom_object",
            "list_cluster_custom_object",
            "patch_namespaced_custom_object",
            "delete_namespaced_custom_object",
        } <= API_OPERATIONS
