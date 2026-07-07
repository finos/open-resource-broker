"""Tests for k8s strategy dispatch surface — provider_api aliases, dry_run propagation, DESCRIBE_RESOURCE_INSTANCES routing, terminal-acquire fulfillment_final promotion, and synthetic instance_type derivation."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operation_outcome import Accepted
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry
from orb.providers.k8s.strategy.k8s_provider_strategy import (
    _all_instances_terminal,
    _outcome_to_provider_result,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry(
    handler: Any | None = None,
    provider_api: str = "Pod",
    api_aliases: dict[str, str] | None = None,
) -> K8sHandlerRegistry:
    from orb.providers.k8s.configuration.config import K8sProviderConfig

    overrides = {provider_api: handler} if handler is not None else {}
    return K8sHandlerRegistry(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        client_provider=MagicMock,
        watch_manager_provider=lambda: None,
        plugin_factories=lambda: {},
        native_spec_service_provider=lambda: None,
        handler_overrides=overrides,
        api_aliases=api_aliases,
    )


def _make_request(
    *,
    provider_api: str = "Pod",
    request_id: str = "req-test",
    pod_names: list[str] | None = None,
    request_type: Any = None,
) -> MagicMock:
    from orb.domain.request.request_types import RequestType

    req = MagicMock()
    req.request_id = request_id
    req.provider_api = provider_api
    req.request_type = request_type or RequestType.ACQUIRE
    req.provider_data = {"pod_names": pod_names or []}
    req.metadata = {}
    return req


def _make_check_result(
    instances: list[dict],
    state: str = "fulfilled",
) -> CheckHostsStatusResult:
    return CheckHostsStatusResult(
        instances=instances,
        fulfilment=ProviderFulfilment(state=state, message="test"),  # type: ignore[arg-type]
    )


def _make_strategy(handler_overrides: dict | None = None) -> Any:
    from orb.providers.k8s.configuration.config import K8sProviderConfig
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    strategy = K8sProviderStrategy(
        config=K8sProviderConfig(),
        logger=MagicMock(),
        handler_overrides=handler_overrides,
    )
    strategy._initialized = True
    return strategy


# ===========================================================================
# Fix 1: fulfillment_final on synchronous Accepted acquires
# ===========================================================================


class TestFulfillmentFinalOnSynchronousAccepted:
    def test_all_instances_terminal_all_running(self) -> None:
        instances = [
            {"instance_id": "pod-0", "status": "running"},
            {"instance_id": "pod-1", "status": "running"},
        ]
        assert _all_instances_terminal(instances) is True

    def test_all_instances_terminal_mixed_terminal_states(self) -> None:
        instances = [
            {"instance_id": "pod-0", "status": "succeeded"},
            {"instance_id": "pod-1", "status": "terminated"},
        ]
        assert _all_instances_terminal(instances) is True

    def test_all_instances_terminal_false_when_pending(self) -> None:
        instances = [
            {"instance_id": "pod-0", "status": "running"},
            {"instance_id": "pod-1", "status": "pending"},
        ]
        assert _all_instances_terminal(instances) is False

    def test_all_instances_terminal_false_for_empty_list(self) -> None:
        # Empty list means no data — cannot claim terminal.
        assert _all_instances_terminal([]) is False

    def test_accepted_with_all_running_instances_gets_fulfillment_final(self) -> None:
        outcome = Accepted(
            request_id="req-sync",
            pending_resource_ids=["pod-0", "pod-1"],
            metadata={
                "instances": [
                    {"instance_id": "pod-0", "status": "running"},
                    {"instance_id": "pod-1", "status": "running"},
                ],
                "provider_api": "Pod",
            },
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.success is True
        assert result.data["provider_data"].get("fulfillment_final") is True

    def test_accepted_with_pending_instances_does_not_get_fulfillment_final(self) -> None:
        outcome = Accepted(
            request_id="req-async",
            pending_resource_ids=["pod-0"],
            metadata={
                "instances": [{"instance_id": "pod-0", "status": "pending"}],
                "provider_api": "Pod",
            },
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.data["provider_data"].get("fulfillment_final") is not True

    def test_accepted_with_no_instances_does_not_get_fulfillment_final(self) -> None:
        outcome = Accepted(
            request_id="req-empty",
            pending_resource_ids=["pod-0"],
            metadata={"instances": [], "provider_api": "Pod"},
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.data["provider_data"].get("fulfillment_final") is not True

    def test_accepted_with_empty_pending_ids_does_not_promote(self) -> None:
        # Empty pending_resource_ids means nothing to track — no promotion.
        outcome = Accepted(
            request_id="req-none",
            pending_resource_ids=[],
            metadata={
                "instances": [{"instance_id": "pod-0", "status": "running"}],
                "provider_api": "Pod",
            },
        )
        result = _outcome_to_provider_result(outcome, fallback_operation="create_instances")
        assert result.data["provider_data"].get("fulfillment_final") is not True


# ===========================================================================
# Fix 2: DESCRIBE_RESOURCE_INSTANCES routing + capability advertisement
# ===========================================================================


class TestDescribeResourceInstancesRouting:
    def test_capabilities_include_describe_resource_instances(self) -> None:
        strategy = _make_strategy()
        caps = strategy.get_capabilities()
        assert ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES in caps.supported_operations

    @pytest.mark.asyncio
    async def test_describe_routes_to_get_status(self) -> None:
        mock_handler = MagicMock()
        mock_handler.check_hosts_status = MagicMock(
            return_value=_make_check_result(
                instances=[{"instance_id": "pod-0", "status": "running"}],
                state="fulfilled",
            )
        )
        strategy = _make_strategy(handler_overrides={"Pod": mock_handler})
        strategy._initialized = True

        req = _make_request(provider_api="Pod")
        operation = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={"request": req, "resource_ids": ["pod-0"]},
        )
        result = await strategy.execute_operation(operation)
        assert result.success is True
        # Verify get_status was exercised (check_hosts_status called).
        mock_handler.check_hosts_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_describe_without_request_returns_error(self) -> None:
        strategy = _make_strategy()
        operation = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={},
        )
        result = await strategy.execute_operation(operation)
        assert result.success is False
        assert "MISSING_REQUEST" in (result.error_code or "")

    @pytest.mark.asyncio
    async def test_describe_surfaces_provider_fulfilment_object_in_metadata(self) -> None:
        """provider_fulfilment must be the ProviderFulfilment object, not a raw string."""
        mock_handler = MagicMock()
        mock_handler.check_hosts_status = MagicMock(
            return_value=_make_check_result(
                instances=[{"instance_id": "pod-0", "status": "running"}],
                state="fulfilled",
            )
        )
        strategy = _make_strategy(handler_overrides={"Pod": mock_handler})
        strategy._initialized = True

        req = _make_request(provider_api="Pod")
        operation = ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={"request": req, "resource_ids": ["pod-0"]},
        )
        result = await strategy.execute_operation(operation)
        assert result.success is True
        fulfilment_val = result.metadata.get("provider_fulfilment")
        # Must be the ProviderFulfilment dataclass, not a plain string.
        assert isinstance(fulfilment_val, ProviderFulfilment), (
            f"Expected ProviderFulfilment, got {type(fulfilment_val)!r}: {fulfilment_val!r}"
        )
        assert fulfilment_val.state == "fulfilled"


# ===========================================================================
# Fix 3: dry_run context propagation
# ===========================================================================


class TestDryRunContextPropagation:
    @pytest.mark.asyncio
    async def test_dry_run_true_returns_synthetic_success(self) -> None:
        strategy = _make_strategy()
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={},
            context={"dry_run": True},
        )
        result = await strategy.execute_operation(operation)
        assert result.success is True
        assert result.data["provider_data"]["dry_run"] is True
        assert result.data["resource_ids"] == []
        assert result.data["instances"] == []

    @pytest.mark.asyncio
    async def test_dry_run_true_sets_fulfillment_final(self) -> None:
        strategy = _make_strategy()
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={},
            context={"dry_run": True},
        )
        result = await strategy.execute_operation(operation)
        assert result.metadata.get("fulfillment_final") is True

    @pytest.mark.asyncio
    async def test_dry_run_false_does_not_short_circuit(self) -> None:
        # When dry_run is False, the normal dispatch path runs and returns
        # an error because no request is provided.
        strategy = _make_strategy()
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={},
            context={"dry_run": False},
        )
        result = await strategy.execute_operation(operation)
        # Normal path: CREATE_INSTANCES without a request → MISSING_REQUEST error.
        assert result.success is False
        assert "MISSING_REQUEST" in (result.error_code or "")

    @pytest.mark.asyncio
    async def test_dry_run_none_context_does_not_short_circuit(self) -> None:
        strategy = _make_strategy()
        operation = ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={},
            context=None,
        )
        result = await strategy.execute_operation(operation)
        assert result.success is False
        assert "MISSING_REQUEST" in (result.error_code or "")


# ===========================================================================
# Fix 4: Synthetic instance_type derived from provider_api
# ===========================================================================


class TestSyntheticInstanceType:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "provider_api,expected_type",
        [
            ("Pod", "k8s/Pod"),
            ("Deployment", "k8s/Deployment"),
            ("StatefulSet", "k8s/StatefulSet"),
            ("Job", "k8s/Job"),
        ],
    )
    async def test_terminated_synthesis_uses_correct_instance_type(
        self, provider_api: str, expected_type: str
    ) -> None:
        from orb.domain.request.request_types import RequestType

        mock_handler = MagicMock()
        mock_handler.check_hosts_status = MagicMock(
            return_value=_make_check_result(instances=[], state="in_progress")
        )
        registry = _make_registry(handler=mock_handler, provider_api=provider_api)

        req = _make_request(
            provider_api=provider_api,
            pod_names=["res-0"],
            request_id="req-synth",
        )
        req.request_type = RequestType.ACQUIRE

        outcome = await registry.get_status(["res-0"], req)
        assert isinstance(outcome, Accepted)
        instances = (outcome.metadata or {}).get("instances", [])
        synth = [i for i in instances if i.get("instance_id") == "res-0"]
        assert len(synth) == 1
        assert synth[0]["instance_type"] == expected_type

    @pytest.mark.asyncio
    async def test_unknown_synthesis_also_uses_derived_instance_type(self) -> None:
        """IDs not in confirmed set get synthetic 'unknown' status, also typed."""
        from orb.domain.request.request_types import RequestType

        mock_handler = MagicMock()
        mock_handler.check_hosts_status = MagicMock(
            return_value=_make_check_result(instances=[], state="in_progress")
        )
        registry = _make_registry(handler=mock_handler, provider_api="Job")

        req = _make_request(provider_api="Job", pod_names=[], request_id="req-job-unknown")
        req.request_type = RequestType.ACQUIRE

        outcome = await registry.get_status(["job-pod-0"], req)
        instances = (outcome.metadata or {}).get("instances", [])
        assert any(i["instance_type"] == "k8s/Job" for i in instances)


# ===========================================================================
# Fix 5: _API_ALIASES for lowercase provider_api variants
# ===========================================================================


class TestApiAliases:
    def test_strategy_has_api_aliases(self) -> None:
        from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

        assert "pod" in K8sProviderStrategy._API_ALIASES
        assert K8sProviderStrategy._API_ALIASES["pod"] == "Pod"
        assert K8sProviderStrategy._API_ALIASES["deployment"] == "Deployment"
        assert K8sProviderStrategy._API_ALIASES["statefulset"] == "StatefulSet"
        assert K8sProviderStrategy._API_ALIASES["job"] == "Job"

    def test_resolve_api_alias_normalises_lowercase(self) -> None:
        strategy = _make_strategy()
        assert strategy.resolve_api_alias("pod") == "Pod"
        assert strategy.resolve_api_alias("deployment") == "Deployment"
        assert strategy.resolve_api_alias("statefulset") == "StatefulSet"
        assert strategy.resolve_api_alias("job") == "Job"

    def test_resolve_api_alias_passthrough_for_canonical(self) -> None:
        strategy = _make_strategy()
        assert strategy.resolve_api_alias("Pod") == "Pod"
        assert strategy.resolve_api_alias("unknown_api") == "unknown_api"

    def test_registry_resolves_lowercase_provider_api_via_aliases(self) -> None:
        mock_handler = MagicMock()
        # Register the handler under the canonical "Pod" key.
        registry = _make_registry(
            handler=mock_handler,
            provider_api="Pod",
            api_aliases={"pod": "Pod", "deployment": "Deployment"},
        )

        req = _make_request(provider_api="pod")  # lowercase submission
        resolved = registry.resolve_provider_api(req)
        assert resolved == "Pod", f"Expected 'Pod', got {resolved!r}"

    def test_registry_get_handler_receives_normalised_key(self) -> None:
        mock_handler = MagicMock()
        registry = _make_registry(
            handler=mock_handler,
            provider_api="Pod",
            api_aliases={"pod": "Pod"},
        )
        # lowercase "pod" must resolve to the canonical "Pod" handler
        handler = registry.get_handler("pod")
        assert handler is mock_handler

    def test_registry_lowercase_without_alias_raises_not_implemented(self) -> None:
        registry = _make_registry()  # no aliases wired
        with pytest.raises(NotImplementedError):
            registry.get_handler("unknown_workload")
