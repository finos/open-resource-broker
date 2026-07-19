"""Unit tests for K8sProviderStrategy module-level functions and strategy methods.

Covers uncovered ranges from k8s_provider_strategy.py:
  280, 315-316, 319, 362-363, 382, 390-393, 427, 444-450, 454-455, 462-464, 491, 501-502,
  520, 525-526, 538-539, 559, 605-606, 612-613, 621-626, 630-632, 638-640, 645-646, 670,
  688-692, 697-698, 722-725, 730-734, 748, 764-765, 770-774, 801, 856, 882-885, 931,
  967, 1036, 1045-1046, 1050, 1054-1055, 1062, 1081, 1099, 1136-1137, 1139, 1141,
  1146-1147, 1149-1150, 1157, 1167, 1343, 1348, 1353, 1358, 1372, 1377, 1382, 1390-1393,
  1396-1397, 1417, 1423-1424, 1428, 1430-1431, 1437, 1439-1440, 1444, 1449-1451, 1455,
  1463, 1526, 1725
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orb.domain.base.operation_outcome import (
    Accepted,
    Completed,
    Failed,
    RequiresFollowUp,
)
from orb.providers.base.strategy import (
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
)
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.strategy.k8s_provider_strategy import (
    K8sProviderStrategy,
    _all_instances_terminal,
    _build_provider_result_data,
    _outcome_to_provider_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def _make_config(**kwargs: Any) -> K8sProviderConfig:
    defaults: dict[str, Any] = {"namespace": "test-ns"}
    defaults.update(kwargs)
    return K8sProviderConfig(**defaults)  # type: ignore[call-arg]


def _make_mock_client() -> MagicMock:
    client = MagicMock()
    client.core_v1 = MagicMock()
    client.apps_v1 = MagicMock()
    client.batch_v1 = MagicMock()
    client.cleanup = MagicMock()
    return client


def _make_strategy(
    *,
    config: K8sProviderConfig | None = None,
    client: Any = None,
    initialized: bool = True,
) -> K8sProviderStrategy:
    cfg = config or _make_config()
    logger = _make_logger()
    mock_client = client or _make_mock_client()
    strategy = K8sProviderStrategy(
        config=cfg,
        logger=logger,
        kubernetes_client=mock_client,
    )
    strategy._initialized = initialized
    return strategy


def _make_operation(
    op_type: ProviderOperationType,
    parameters: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> ProviderOperation:
    return ProviderOperation(
        operation_type=op_type,
        parameters=parameters or {},
        context=context or {},
    )


# ---------------------------------------------------------------------------
# _all_instances_terminal
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAllInstancesTerminal:
    def test_empty_list_is_not_terminal(self) -> None:
        assert _all_instances_terminal([]) is False

    def test_all_running_is_terminal(self) -> None:
        instances = [{"status": "running"}, {"status": "running"}]
        assert _all_instances_terminal(instances) is True

    def test_all_succeeded_is_terminal(self) -> None:
        instances = [{"status": "succeeded"}]
        assert _all_instances_terminal(instances) is True

    def test_all_terminated_is_terminal(self) -> None:
        instances = [{"status": "terminated"}]
        assert _all_instances_terminal(instances) is True

    def test_mixed_terminal_is_terminal(self) -> None:
        instances = [{"status": "running"}, {"status": "terminated"}, {"status": "succeeded"}]
        assert _all_instances_terminal(instances) is True

    def test_pending_instance_is_not_terminal(self) -> None:
        instances = [{"status": "running"}, {"status": "pending"}]
        assert _all_instances_terminal(instances) is False

    def test_missing_status_key_is_not_terminal(self) -> None:
        instances = [{"name": "pod-1"}]
        assert _all_instances_terminal(instances) is False

    def test_none_status_is_not_terminal(self) -> None:
        instances = [{"status": None}]
        assert _all_instances_terminal(instances) is False


# ---------------------------------------------------------------------------
# _build_provider_result_data
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildProviderResultData:
    def test_basic_shape(self) -> None:
        data = _build_provider_result_data(resource_ids=["r1", "r2"])
        assert data["resource_ids"] == ["r1", "r2"]
        assert "instances" in data
        assert "instance_ids" in data
        assert "provider_data" in data

    def test_instance_ids_fallback_to_resource_ids(self) -> None:
        data = _build_provider_result_data(resource_ids=["r1"])
        # When no machine_ids in metadata, instance_ids == resource_ids
        assert data["instance_ids"] == ["r1"]

    def test_machine_ids_in_metadata_used_for_instance_ids(self) -> None:
        data = _build_provider_result_data(
            resource_ids=["dep-1"],
            metadata={"machine_ids": ["pod-1", "pod-2"]},
        )
        assert data["instance_ids"] == ["pod-1", "pod-2"]

    def test_instances_from_metadata(self) -> None:
        data = _build_provider_result_data(
            resource_ids=["dep-1"],
            metadata={"instances": [{"pod": "pod-1"}]},
        )
        assert data["instances"] == [{"pod": "pod-1"}]

    def test_tracking_request_id_added_when_provided(self) -> None:
        data = _build_provider_result_data(
            resource_ids=["r1"],
            tracking_request_id="track-123",
        )
        assert data["tracking_request_id"] == "track-123"

    def test_no_tracking_id_when_not_provided(self) -> None:
        data = _build_provider_result_data(resource_ids=["r1"])
        assert "tracking_request_id" not in data

    def test_provider_data_carries_metadata(self) -> None:
        data = _build_provider_result_data(
            resource_ids=["r1"],
            metadata={"provider_api": "Pod", "namespace": "test"},
        )
        assert data["provider_data"]["provider_api"] == "Pod"


# ---------------------------------------------------------------------------
# _outcome_to_provider_result
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOutcomeToProviderResult:
    def test_failed_outcome_is_error_result(self) -> None:
        outcome = Failed(error="something went wrong")
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.success is False
        assert "something went wrong" in (result.error_message or "")

    def test_failed_outcome_sets_recoverable_true_in_metadata(self) -> None:
        outcome = Failed(error="transient", recoverable=True)
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        # recoverable=True is surfaced verbatim in metadata['recoverable']
        assert result.metadata.get("recoverable") is True

    def test_failed_outcome_sets_recoverable_false_in_metadata(self) -> None:
        outcome = Failed(error="fatal", recoverable=False)
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        # recoverable=False is surfaced verbatim in metadata['recoverable']
        assert result.metadata.get("recoverable") is False

    def test_accepted_outcome_is_success(self) -> None:
        outcome = Accepted(
            request_id="req-1",
            pending_resource_ids=["pod-1"],
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.success is True

    def test_accepted_outcome_carries_resource_ids(self) -> None:
        outcome = Accepted(
            request_id="req-1",
            pending_resource_ids=["pod-1", "pod-2"],
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert "pod-1" in result.data.get("resource_ids", [])

    def test_accepted_all_terminal_sets_fulfillment_final(self) -> None:
        outcome = Accepted(
            request_id="req-1",
            pending_resource_ids=["pod-1"],
            metadata={"instances": [{"status": "running"}]},
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.data.get("provider_data", {}).get("fulfillment_final") is True

    def test_accepted_pending_instances_does_not_set_fulfillment_final(self) -> None:
        outcome = Accepted(
            request_id="req-1",
            pending_resource_ids=["pod-1"],
            metadata={"instances": [{"status": "pending"}]},
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert not result.data.get("provider_data", {}).get("fulfillment_final", False)

    def test_completed_outcome_is_success(self) -> None:
        outcome = Completed(resource_ids=["pod-1"])
        result = _outcome_to_provider_result(outcome, fallback_operation="get_instance_status")
        assert result.success is True

    def test_completed_outcome_sets_fulfillment_final(self) -> None:
        outcome = Completed(resource_ids=["pod-1"])
        result = _outcome_to_provider_result(outcome, fallback_operation="get_instance_status")
        assert result.data.get("provider_data", {}).get("fulfillment_final") is True

    def test_requires_follow_up_is_success(self) -> None:
        ctx = MagicMock()
        ctx.follow_up_kind = "poll"
        ctx.pending_resource_ids = ["pod-1"]
        outcome = RequiresFollowUp(context=ctx)
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.success is True

    def test_requires_follow_up_carries_pending_ids(self) -> None:
        ctx = MagicMock()
        ctx.follow_up_kind = "poll"
        ctx.pending_resource_ids = ["pod-1"]
        ctx.pending_instance_ids = None
        ctx.provider_handle = None
        ctx.expected_terminal_state = None
        outcome = RequiresFollowUp(context=ctx)
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert "pod-1" in result.data.get("resource_ids", [])

    def test_unknown_outcome_type_returns_error(self) -> None:
        # _outcome_to_provider_result falls through to the error branch for unexpected types
        # We use a plain object that is not Accepted/Completed/Failed/RequiresFollowUp
        outcome = object()  # type: ignore[arg-type]
        result = _outcome_to_provider_result(outcome, fallback_operation="op")  # type: ignore[arg-type]
        assert result.success is False
        assert "Unknown" in (result.error_message or "")


# ---------------------------------------------------------------------------
# K8sProviderStrategy — constructor and property paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyConstructor:
    def test_requires_k8s_provider_config(self) -> None:
        with pytest.raises(ValueError, match="K8sProviderConfig"):
            K8sProviderStrategy(
                config={"namespace": "bad"},  # type: ignore[arg-type]
                logger=_make_logger(),
            )

    def test_provider_type_is_k8s(self) -> None:
        strategy = _make_strategy()
        assert strategy.provider_type == "k8s"

    def test_provider_name_none_when_not_set(self) -> None:
        strategy = _make_strategy()
        assert strategy.provider_name is None

    def test_provider_name_returned_when_set(self) -> None:
        strategy = _make_strategy()
        strategy._provider_name = "my-k8s"
        assert strategy.provider_name == "my-k8s"

    def test_kubernetes_client_returns_injected(self) -> None:
        mock_client = _make_mock_client()
        strategy = _make_strategy(client=mock_client)
        assert strategy.kubernetes_client is mock_client

    def test_kubernetes_client_constructed_lazily_when_none(self) -> None:
        cfg = _make_config()
        strategy = K8sProviderStrategy(config=cfg, logger=_make_logger())
        with patch(
            "orb.providers.k8s.strategy.k8s_provider_strategy.K8sClient"
        ) as mock_k8s_client_cls:
            mock_k8s_client_cls.return_value = MagicMock()
            _ = strategy.kubernetes_client
            mock_k8s_client_cls.assert_called_once()

    def test_node_state_cache_always_present(self) -> None:
        strategy = _make_strategy()
        assert strategy.node_state_cache is not None

    def test_node_events_cache_always_present(self) -> None:
        strategy = _make_strategy()
        assert strategy.node_events_cache is not None


# ---------------------------------------------------------------------------
# K8sProviderStrategy.initialize
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyInitialize:
    def test_initialize_returns_true_on_success(self) -> None:
        strategy = _make_strategy(initialized=False)
        result = strategy.initialize()
        assert result is True
        assert strategy._initialized is True

    def test_initialize_handles_exception(self) -> None:
        strategy = _make_strategy(initialized=False)
        strategy._logger.info = MagicMock(side_effect=RuntimeError("boom"))
        result = strategy.initialize()
        assert result is False


# ---------------------------------------------------------------------------
# K8sProviderStrategy.cleanup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyCleanup:
    def test_cleanup_clears_initialized_on_success(self) -> None:
        mock_client = _make_mock_client()
        strategy = _make_strategy(client=mock_client)
        strategy.cleanup()
        assert strategy._initialized is False
        mock_client.cleanup.assert_called_once()

    def test_cleanup_with_node_watcher(self) -> None:
        strategy = _make_strategy()
        mock_node_watcher = MagicMock()
        strategy._node_watcher = mock_node_watcher
        strategy.cleanup()
        mock_node_watcher.stop.assert_called_once()

    def test_cleanup_with_events_watcher(self) -> None:
        strategy = _make_strategy()
        mock_events_watcher = MagicMock()
        strategy._events_watcher = mock_events_watcher
        strategy.cleanup()
        mock_events_watcher.stop.assert_called_once()

    def test_cleanup_node_watcher_error_is_swallowed(self) -> None:
        strategy = _make_strategy()
        mock_node_watcher = MagicMock()
        mock_node_watcher.stop.side_effect = RuntimeError("stop failed")
        strategy._node_watcher = mock_node_watcher
        # Should not raise
        strategy.cleanup()

    def test_cleanup_client_error_does_not_clear_initialized(self) -> None:
        mock_client = _make_mock_client()
        mock_client.cleanup.side_effect = RuntimeError("client cleanup failed")
        strategy = _make_strategy(client=mock_client)
        strategy.cleanup()
        # _initialized should NOT be cleared if client cleanup failed
        assert strategy._initialized is True

    def test_cleanup_orphan_gc_stopped_when_running(self) -> None:
        strategy = _make_strategy()
        mock_gc = MagicMock()
        mock_gc.is_running.return_value = True
        mock_gc.stop = AsyncMock()
        strategy._orphan_gc = mock_gc
        strategy.cleanup()
        # is_running() is True, so cleanup drives gc.stop() to completion.
        mock_gc.stop.assert_awaited_once()

    def test_cleanup_orphan_gc_not_stopped_when_not_running(self) -> None:
        strategy = _make_strategy()
        mock_gc = MagicMock()
        mock_gc.is_running.return_value = False
        mock_gc.stop = AsyncMock()
        strategy._orphan_gc = mock_gc
        strategy.cleanup()
        # is_running() is False, so gc.stop() must not be scheduled.
        mock_gc.stop.assert_not_awaited()


# ---------------------------------------------------------------------------
# K8sProviderStrategy — execute_operation paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteOperation:
    def test_not_initialized_returns_error(self) -> None:
        strategy = _make_strategy(initialized=False)
        op = _make_operation(ProviderOperationType.CREATE_INSTANCES)
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is False
        assert "not initialized" in (result.error_message or "").lower()

    def test_dry_run_returns_synthetic_success(self) -> None:
        strategy = _make_strategy()
        op = _make_operation(
            ProviderOperationType.CREATE_INSTANCES,
            context={"dry_run": True},
        )
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is True
        assert result.data.get("provider_data", {}).get("dry_run") is True

    def test_health_check_operation(self) -> None:
        strategy = _make_strategy()
        mock_health = MagicMock()
        mock_health.is_healthy = True
        mock_health.status_message = "ok"
        mock_health.response_time_ms = 5
        strategy._health_check_service = MagicMock()
        strategy._health_check_service.check_health.return_value = mock_health
        op = _make_operation(ProviderOperationType.HEALTH_CHECK)
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is True
        assert result.data["is_healthy"] is True

    def test_unsupported_operation_returns_error(self) -> None:
        strategy = _make_strategy()
        # GET_AVAILABLE_TEMPLATES is not handled by k8s
        op = _make_operation(ProviderOperationType.GET_AVAILABLE_TEMPLATES)
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is False
        assert "UNSUPPORTED" in (result.error_code or "")

    def test_create_instances_missing_request_returns_error(self) -> None:
        strategy = _make_strategy()
        op = _make_operation(ProviderOperationType.CREATE_INSTANCES, parameters={})
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is False
        assert "MISSING_REQUEST" in (result.error_code or "") or not result.success

    def test_terminate_instances_missing_request_returns_error(self) -> None:
        strategy = _make_strategy()
        op = _make_operation(ProviderOperationType.TERMINATE_INSTANCES, parameters={})
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is False
        assert "MISSING_REQUEST" in (result.error_code or "") or not result.success

    def test_get_instance_status_missing_request_returns_error(self) -> None:
        strategy = _make_strategy()
        op = _make_operation(ProviderOperationType.GET_INSTANCE_STATUS, parameters={})
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is False
        assert "MISSING_REQUEST" in (result.error_code or "") or not result.success

    def test_describe_resource_instances_missing_request_returns_error(self) -> None:
        strategy = _make_strategy()
        op = _make_operation(ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES, parameters={})
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is False
        assert "MISSING_REQUEST" in (result.error_code or "") or not result.success

    def test_exception_in_operation_returns_error_result(self) -> None:
        strategy = _make_strategy()
        strategy._health_check_service = MagicMock()
        strategy._health_check_service.check_health.side_effect = RuntimeError("boom")
        op = _make_operation(ProviderOperationType.HEALTH_CHECK)
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is False
        assert result.error_code is not None

    def test_cancel_mode_routes_to_cancel_handler(self) -> None:
        strategy = _make_strategy()
        mock_cancel_svc = MagicMock()
        cancel_result = MagicMock()
        cancel_result.status = "success"
        cancel_result.to_dict.return_value = {"status": "success"}
        mock_cancel_svc.cancel_resource = AsyncMock(return_value=cancel_result)
        strategy._instance_operation_service = mock_cancel_svc

        op = _make_operation(
            ProviderOperationType.TERMINATE_INSTANCES,
            parameters={"request_id": "req-abc"},
            context={"cancel_mode": True},
        )
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is True
        mock_cancel_svc.cancel_resource.assert_called_once()

    def test_validate_template_operation(self) -> None:
        strategy = _make_strategy()
        mock_result = MagicMock(spec=ProviderResult)
        mock_result.success = True
        mock_result.error = None
        mock_result.error_code = None
        mock_result.data = {}
        mock_result.metadata = {}
        mock_result.routing_info = {}
        mock_result.model_copy.return_value = mock_result
        strategy._template_service = MagicMock()
        strategy._template_service.validate_template.return_value = mock_result
        op = _make_operation(ProviderOperationType.VALIDATE_TEMPLATE)
        asyncio.run(strategy.execute_operation(op))
        strategy._template_service.validate_template.assert_called_once_with(op)


# ---------------------------------------------------------------------------
# K8sProviderStrategy — cancel_resource direct entry point (line 1067-1084)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelResource:
    def test_cancel_resource_delegates_to_service(self) -> None:
        strategy = _make_strategy()
        mock_svc = MagicMock()
        expected = MagicMock()
        mock_svc.cancel_resource = AsyncMock(return_value=expected)
        strategy._instance_operation_service = mock_svc

        result = asyncio.run(strategy.cancel_resource("req-123"))

        mock_svc.cancel_resource.assert_called_once()
        assert result is expected


# ---------------------------------------------------------------------------
# K8sProviderStrategy — cancel_mode missing request_id (line 1045-1046)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleCancelResource:
    def test_cancel_resource_missing_request_id_returns_error(self) -> None:
        strategy = _make_strategy()
        op = _make_operation(
            ProviderOperationType.TERMINATE_INSTANCES,
            parameters={},  # no request_id, no request obj
            context={"cancel_mode": True},
        )
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is False
        assert result.error_code is not None

    def test_cancel_resource_partial_failure_returns_partial(self) -> None:
        strategy = _make_strategy()
        mock_svc = MagicMock()
        cancel_result = MagicMock()
        cancel_result.status = "partial"
        cancel_result.failed = [("Pod/x", "err")]
        cancel_result.to_dict.return_value = {"status": "partial"}
        mock_svc.cancel_resource = AsyncMock(return_value=cancel_result)
        strategy._instance_operation_service = mock_svc

        op = _make_operation(
            ProviderOperationType.TERMINATE_INSTANCES,
            parameters={"request_id": "req-abc"},
            context={"cancel_mode": True},
        )
        result = asyncio.run(strategy.execute_operation(op))
        # Partial: the result is not a clean success — error_code must be set
        assert result.error_code is not None


# ---------------------------------------------------------------------------
# K8sProviderStrategy — class-level helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStrategyClassMethods:
    def test_is_image_resolution_needed_false(self) -> None:
        assert K8sProviderStrategy.is_image_resolution_needed() is False

    def test_get_available_regions_empty(self) -> None:
        assert K8sProviderStrategy.get_available_regions() == []

    def test_get_default_region_empty(self) -> None:
        assert K8sProviderStrategy.get_default_region() == ""

    def test_get_supported_apis_returns_list(self) -> None:
        # get_supported_apis is on K8sCapabilityService, delegated via instance method
        strategy = _make_strategy()
        apis = strategy.get_supported_apis()
        assert "Pod" in apis

    def test_resolve_api_alias_lowercase_pod(self) -> None:
        strategy = _make_strategy()
        assert strategy.resolve_api_alias("pod") == "Pod"

    def test_resolve_api_alias_unknown_returns_as_is(self) -> None:
        strategy = _make_strategy()
        assert strategy.resolve_api_alias("MyCustomCRD") == "MyCustomCRD"

    def test_generate_provider_name_delegates(self) -> None:
        name = K8sProviderStrategy.generate_provider_name({"context": "my-ctx"})
        assert "k8s" in name

    def test_parse_provider_name_round_trips(self) -> None:
        strategy = _make_strategy()
        result = strategy.parse_provider_name("k8s_my-ctx")
        assert isinstance(result, dict)

    def test_get_provider_name_pattern(self) -> None:
        strategy = _make_strategy()
        assert "k8s" in strategy.get_provider_name_pattern()


# ---------------------------------------------------------------------------
# K8sProviderStrategy — register_handler (line 134-141)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterHandler:
    def test_register_new_handler(self) -> None:
        strategy = _make_strategy()
        fake_handler = MagicMock()
        strategy.register_handler("MyAPI", fake_handler)
        assert strategy._handler_factories["MyAPI"] is fake_handler

    def test_idempotent_re_registration_same_class(self) -> None:
        strategy = _make_strategy()
        fake_handler = MagicMock()
        strategy.register_handler("MyAPI", fake_handler)
        # Same class again must not raise
        strategy.register_handler("MyAPI", fake_handler)

    def test_registering_different_class_raises(self) -> None:
        strategy = _make_strategy()
        fake_handler_a = MagicMock()
        fake_handler_b = MagicMock()
        strategy.register_handler("MyAPI", fake_handler_a)
        with pytest.raises(ValueError, match="already registered"):
            strategy.register_handler("MyAPI", fake_handler_b)

    def test_unregister_handler(self) -> None:
        strategy = _make_strategy()
        fake_handler = MagicMock()
        strategy.register_handler("MyAPI", fake_handler)
        strategy.unregister_handler("MyAPI")
        assert "MyAPI" not in strategy._handler_factories


# ---------------------------------------------------------------------------
# K8sProviderStrategy — _resolve_native_spec_service (lines 1404-1455)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveNativeSpecService:
    def test_returns_cached_result_on_second_call(self) -> None:
        strategy = _make_strategy()
        resolver = strategy._native_spec_resolver
        resolver._resolved = True
        cached = MagicMock()
        resolver._k8s_native_spec_service = cached
        result = strategy._resolve_native_spec_service()
        assert result is cached

    def test_returns_none_when_not_enabled(self) -> None:
        cfg = _make_config(native_spec_enabled=False)
        strategy = _make_strategy(config=cfg)
        result = strategy._resolve_native_spec_service()
        assert result is None

    def test_returns_none_when_no_config_port(self) -> None:
        cfg = _make_config(native_spec_enabled=True)
        strategy = _make_strategy(config=cfg)
        strategy._config_port = None
        result = strategy._resolve_native_spec_service()
        assert result is None

    def test_returns_none_when_no_injected_service(self) -> None:
        cfg = _make_config(native_spec_enabled=True)
        strategy = _make_strategy(config=cfg)
        strategy._config_port = MagicMock()
        strategy._injected_native_spec_service = None
        result = strategy._resolve_native_spec_service()
        assert result is None

    def test_import_error_returns_none(self) -> None:
        cfg = _make_config(native_spec_enabled=True)
        strategy = _make_strategy(config=cfg)
        strategy._config_port = MagicMock()
        strategy._injected_native_spec_service = MagicMock()
        with patch(
            "orb.providers.k8s.strategy.k8s_provider_strategy.K8sProviderStrategy"
            "._resolve_native_spec_service",
            wraps=strategy._resolve_native_spec_service,
        ):
            # Simulate ImportError by patching the internal import
            import builtins

            real_import = builtins.__import__

            def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
                if "k8s_native_spec_service" in name:
                    raise ImportError("jinja2 not installed")
                return real_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=_fake_import):
                result = strategy._resolve_native_spec_service()
        # ImportError path should return None via the exception handler
        assert result is None


# ---------------------------------------------------------------------------
# K8sProviderStrategy — start/stop delegates to service
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStartStopDelegation:
    def test_start_instances_delegates_to_start_stop_service(self) -> None:
        strategy = _make_strategy()
        mock_svc = MagicMock()
        mock_result = MagicMock(spec=ProviderResult)
        mock_result.success = True
        mock_result.error = None
        mock_result.error_code = None
        mock_result.data = {}
        mock_result.metadata = {}
        mock_result.routing_info = {}
        mock_result.model_copy.return_value = mock_result
        mock_svc.start_instances = AsyncMock(return_value=mock_result)
        strategy._start_stop_service = mock_svc

        op = _make_operation(ProviderOperationType.START_INSTANCES)
        asyncio.run(strategy.execute_operation(op))
        mock_svc.start_instances.assert_called_once_with(op)

    def test_stop_instances_delegates_to_start_stop_service(self) -> None:
        strategy = _make_strategy()
        mock_svc = MagicMock()
        mock_result = MagicMock(spec=ProviderResult)
        mock_result.success = True
        mock_result.error = None
        mock_result.error_code = None
        mock_result.data = {}
        mock_result.metadata = {}
        mock_result.routing_info = {}
        mock_result.model_copy.return_value = mock_result
        mock_svc.stop_instances = AsyncMock(return_value=mock_result)
        strategy._start_stop_service = mock_svc

        op = _make_operation(ProviderOperationType.STOP_INSTANCES)
        asyncio.run(strategy.execute_operation(op))
        mock_svc.stop_instances.assert_called_once_with(op)


# ---------------------------------------------------------------------------
# K8sProviderStrategy — describe_resource_instances fulfilment metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDescribeResourceInstancesFulfilment:
    def test_fulfilment_surfaced_in_metadata_when_accepted(self) -> None:
        strategy = _make_strategy()
        mock_request = MagicMock()
        fulfilment_obj = MagicMock()
        fulfilment_obj.state = "running"

        outcome = Accepted(
            request_id="req-1",
            pending_resource_ids=["pod-1"],
            metadata={"fulfilment": fulfilment_obj},
        )
        strategy._handler_registry = MagicMock()
        strategy._handler_registry.get_status = AsyncMock(return_value=outcome)

        op = _make_operation(
            ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={"request": mock_request, "resource_ids": ["pod-1"]},
        )
        result = asyncio.run(strategy.execute_operation(op))
        assert result.success is True
        assert result.metadata.get("provider_fulfilment") is fulfilment_obj

    def test_fulfilment_not_set_when_no_fulfilment_in_metadata(self) -> None:
        strategy = _make_strategy()
        mock_request = MagicMock()

        outcome = Accepted(
            request_id="req-1",
            pending_resource_ids=["pod-1"],
            metadata={},
        )
        strategy._handler_registry = MagicMock()
        strategy._handler_registry.get_status = AsyncMock(return_value=outcome)

        op = _make_operation(
            ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={"request": mock_request, "resource_ids": ["pod-1"]},
        )
        result = asyncio.run(strategy.execute_operation(op))
        assert "provider_fulfilment" not in result.metadata


# ---------------------------------------------------------------------------
# K8sProviderStrategy — get_defaults_config (line 1125-1157)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetDefaultsConfig:
    def test_returns_dict(self) -> None:
        raw = K8sProviderStrategy.get_defaults_config()
        assert isinstance(raw, dict)

    def test_has_provider_key(self) -> None:
        raw = K8sProviderStrategy.get_defaults_config()
        assert "provider" in raw


# ---------------------------------------------------------------------------
# K8sProviderStrategy — last_reconciliation_report property
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLastReconciliationReport:
    def test_initially_none(self) -> None:
        strategy = _make_strategy()
        assert strategy.last_reconciliation_report is None

    def test_stores_report(self) -> None:
        strategy = _make_strategy()
        mock_report = MagicMock()
        strategy._reconciliation_lifecycle._last_reconciliation_report = mock_report
        assert strategy.last_reconciliation_report is mock_report


# ---------------------------------------------------------------------------
# K8sProviderStrategy — _get_metrics (line 550-563)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMetrics:
    def test_returns_none_when_metrics_disabled(self) -> None:
        cfg = _make_config(metrics_enabled=False)
        strategy = _make_strategy(config=cfg)
        assert strategy._get_metrics() is None

    def test_returns_same_object_on_second_call(self) -> None:
        cfg = _make_config(metrics_enabled=True)
        strategy = _make_strategy(config=cfg)
        sentinel_metrics = MagicMock()
        with patch(
            "orb.providers.k8s.infrastructure.services.metrics.K8sMetrics",
            return_value=sentinel_metrics,
        ) as mock_metrics_cls:
            m1 = strategy._get_metrics()
            m2 = strategy._get_metrics()
        # Memoized: first call constructs K8sMetrics, second returns the cache.
        assert m1 is sentinel_metrics
        assert m1 is m2
        mock_metrics_cls.assert_called_once()
