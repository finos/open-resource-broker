"""Tests for async correctness guards in ProvisioningOrchestrationService.

Covers:
- asyncio.timeout wrapping the execute_operation call in _dispatch_single_attempt
- asyncio.to_thread offloading _persist_acquiring to a worker thread
"""

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
)
from orb.domain.base.results import ProviderSelectionResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(dispatch_timeout: float = 10.0) -> ProvisioningOrchestrationService:
    """Build a service with all mocks wired; dispatch_timeout_seconds from config."""
    container = MagicMock()
    logger = MagicMock()
    provider_selection_port = MagicMock()
    provider_config_port = MagicMock()
    config_port = MagicMock()
    circuit_breaker_factory = MagicMock()

    config_port.get_request_config.return_value = {
        "dispatch_timeout_seconds": dispatch_timeout,
    }

    cb = MagicMock()
    cb.has_state.return_value = False
    circuit_breaker_factory.return_value = cb

    return ProvisioningOrchestrationService(
        container=container,
        logger=logger,
        provider_selection_port=provider_selection_port,
        provider_config_port=provider_config_port,
        config_port=config_port,
        circuit_breaker_factory=circuit_breaker_factory,
    )


def _make_request(count: int = 1):
    request = MagicMock()
    request.request_id = "req-timeout-test"
    request.requested_count = count
    request.metadata = {}
    request.update_metadata = lambda d: request
    return request


def _make_template():
    template = MagicMock()
    template.template_id = "tmpl-timeout"
    return template


def _make_selection_result(provider_name: str = "aws_default_us-east-1") -> ProviderSelectionResult:
    return ProviderSelectionResult(
        provider_name=provider_name,
        provider_type="aws",
        selection_reason="test",
        confidence=1.0,
    )


# ---------------------------------------------------------------------------
# asyncio.timeout tests
# ---------------------------------------------------------------------------


class TestDispatchTimeout:
    """_dispatch_single_attempt must time out when the operation hangs."""

    @pytest.mark.asyncio
    async def test_timeout_returns_failure_result(self):
        """When execute_operation hangs beyond the timeout, a failed ProvisioningResult is returned."""
        svc = _make_service(dispatch_timeout=0.05)  # 50 ms — fast for tests

        # Simulate a hung provider: never resolves
        async def _hang(*_args, **_kwargs):
            await asyncio.sleep(60)  # much longer than the 50 ms timeout

        svc._provider_selection_port.execute_operation = _hang

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        cast(MagicMock, svc._container).get.return_value = scheduler

        result = await svc._dispatch_single_attempt(
            _make_template(),
            _make_request(),
            _make_selection_result(),
            count=1,
            dispatch_timeout_seconds=0.05,
        )

        assert result.success is False
        assert result.is_final is True
        assert "timed out" in (result.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_timeout_logs_warning(self):
        """A warning is logged when the dispatch times out."""
        svc = _make_service(dispatch_timeout=0.05)

        async def _hang(*_args, **_kwargs):
            await asyncio.sleep(60)

        svc._provider_selection_port.execute_operation = _hang

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        cast(MagicMock, svc._container).get.return_value = scheduler

        await svc._dispatch_single_attempt(
            _make_template(),
            _make_request(),
            _make_selection_result(),
            count=1,
            dispatch_timeout_seconds=0.05,
        )

        cast(MagicMock, svc._logger).warning.assert_called()
        warning_call_args = str(cast(MagicMock, svc._logger).warning.call_args_list)
        assert "timed out" in warning_call_args.lower() or "timeout" in warning_call_args.lower()

    @pytest.mark.asyncio
    async def test_fast_operation_completes_normally(self):
        """An operation that completes within the timeout is not interrupted."""
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        svc = _make_service(dispatch_timeout=10.0)

        provider_result = ProviderResult.success_result(
            data={
                "resource_ids": ["i-ok"],
                "instances": [{"id": "i-ok"}],
                "instance_ids": ["i-ok"],
            },
            metadata={},
        )
        svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        cast(MagicMock, svc._container).get.return_value = scheduler

        result = await svc._dispatch_single_attempt(
            _make_template(),
            _make_request(),
            _make_selection_result(),
            count=1,
            dispatch_timeout_seconds=10.0,
        )

        assert result.success is True
        assert result.resource_ids == ["i-ok"]

    @pytest.mark.asyncio
    async def test_timeout_fires_via_execute_provisioning_config(self):
        """dispatch_timeout_seconds from config flows through to the dispatch call."""
        svc = _make_service(dispatch_timeout=0.05)

        call_count = 0

        async def _hang(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(60)

        svc._provider_selection_port.execute_operation = _hang

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        cast(MagicMock, svc._container).get.return_value = scheduler

        request = _make_request(count=1)
        # Make update_metadata return a fresh mock that also has metadata={} and matching attrs
        updated_req = MagicMock()
        updated_req.request_id = "req-timeout-test"
        updated_req.requested_count = 1
        updated_req.metadata = {}
        updated_req.update_metadata = lambda d: updated_req
        request.update_metadata = lambda d: updated_req

        result = await svc.execute_provisioning(_make_template(), request, _make_selection_result())

        # At least one attempt was made and resulted in failure due to timeout
        assert call_count >= 1
        assert result.success is False


# ---------------------------------------------------------------------------
# Accepted-outcome short-circuit
# ---------------------------------------------------------------------------


class TestAcceptedOutcomeBreaksRetryLoop:
    """Async providers that return Accepted must not be retried.

    A retry creates a SECOND fleet / batch alongside the one the provider
    already accepted; downstream status-check then sees one healthy fleet and
    N-1 empty fleets and flips the request to ``complete_with_error``.

    This contract replaces the previous retry-loop tests against
    ``_persist_acquiring``: the persist hook only ran on the retry path,
    and the retry path now exits immediately on ``Accepted`` (the only
    outcome any in-tree provider emits when more attempts could possibly
    help).  If a future provider emits ``RequiresFollowUp``, the retry
    persist path becomes reachable again and tests should be restored.
    """

    @pytest.mark.asyncio
    async def test_accepted_outcome_exits_loop_after_one_attempt(self):
        """A single Accepted attempt must end the loop even with remaining > 0."""
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        svc = _make_service(dispatch_timeout=10.0)

        accepted_result = ProviderResult.success_result(
            data={
                "resource_ids": ["fleet-abc"],
                "instances": [],
                "instance_ids": [],
            },
            # fulfillment_final omitted (defaults to False) → Accepted outcome.
            metadata={},
        )
        svc._provider_selection_port.execute_operation = AsyncMock(
            side_effect=[accepted_result, accepted_result, accepted_result]
        )

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        cast(MagicMock, svc._container).get.return_value = scheduler

        request = MagicMock()
        request.request_id = "req-accepted-once"
        request.requested_count = 2
        request.metadata = {}
        request.update_metadata = lambda d: request
        request.update_status = MagicMock(return_value=request)

        result = await svc.execute_provisioning(_make_template(), request, _make_selection_result())

        # Exactly one provider call: the Accepted outcome short-circuits the
        # loop before another attempt can be made.
        assert svc._provider_selection_port.execute_operation.await_count == 1
        # The single accepted fleet is recorded in the returned resource_ids.
        assert result.resource_ids == ["fleet-abc"]
