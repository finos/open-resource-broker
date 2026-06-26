"""Unit tests for ProvisioningOrchestrationService — OperationOutcome integration.

Covers:
- ProvisioningResult.outcome is set for all return paths
- ProvisioningResult.is_final is correctly derived from outcome via __post_init__
- assert_never exhaustiveness compiles (no missed union branches)
"""

from typing import assert_never
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.services.provisioning_orchestration_service import (
    ProvisioningOrchestrationService,
    ProvisioningResult,
)
from orb.domain.base.operation_outcome import (
    Accepted,
    Completed,
    Failed,
    OperationOutcome,
    RequiresFollowUp,
)
from orb.domain.base.results import ProviderSelectionResult

# ---------------------------------------------------------------------------
# ProvisioningResult — outcome/is_final derivation
# ---------------------------------------------------------------------------


class TestProvisioningResultOutcomeDerivation:
    """is_final must be derived from outcome when outcome is present."""

    def test_accepted_sets_is_final_false(self):
        result = ProvisioningResult(
            success=True,
            resource_ids=["r-1"],
            machine_ids=[],
            instances=[],
            provider_data={},
            outcome=Accepted(request_id="req-1", pending_resource_ids=["r-1"]),
        )
        assert result.is_final is False

    def test_completed_sets_is_final_true(self):
        result = ProvisioningResult(
            success=True,
            resource_ids=["r-1"],
            machine_ids=["i-1"],
            instances=[{"id": "i-1"}],
            provider_data={},
            outcome=Completed(resource_ids=["r-1"]),
        )
        assert result.is_final is True

    def test_failed_sets_is_final_true(self):
        result = ProvisioningResult(
            success=False,
            resource_ids=[],
            machine_ids=[],
            instances=[],
            provider_data={},
            outcome=Failed(error="something went wrong"),
        )
        assert result.is_final is True

    def test_recoverable_failed_sets_is_final_true(self):
        result = ProvisioningResult(
            success=False,
            resource_ids=[],
            machine_ids=[],
            instances=[],
            provider_data={},
            outcome=Failed(error="timeout", recoverable=True),
        )
        assert result.is_final is True

    def test_requires_follow_up_sets_is_final_false(self):
        from orb.application.services.request_follow_up_context import (
            TerminationFollowUpContext,
        )

        ctx = TerminationFollowUpContext(pending_instance_ids=["i-1"])
        result = ProvisioningResult(
            success=True,
            resource_ids=[],
            machine_ids=[],
            instances=[],
            provider_data={},
            outcome=RequiresFollowUp(context=ctx),
        )
        assert result.is_final is False

    def test_no_outcome_honours_explicit_is_final_true(self):
        """Legacy path: when outcome is None, explicit is_final is preserved."""
        result = ProvisioningResult(
            success=True,
            resource_ids=[],
            machine_ids=[],
            instances=[],
            provider_data={},
            is_final=True,
        )
        assert result.is_final is True

    def test_no_outcome_honours_explicit_is_final_false(self):
        """Legacy path: when outcome is None, explicit is_final=False is preserved."""
        result = ProvisioningResult(
            success=True,
            resource_ids=[],
            machine_ids=[],
            instances=[],
            provider_data={},
            is_final=False,
        )
        assert result.is_final is False


# ---------------------------------------------------------------------------
# OperationOutcome exhaustiveness — assert_never
# ---------------------------------------------------------------------------


class TestOperationOutcomeExhaustiveness:
    """assert_never must cover all union branches without static errors."""

    def test_all_union_branches_handled(self):
        """Exhaustive match over all OperationOutcome variants must not raise."""
        outcomes: list[OperationOutcome] = [
            Accepted(request_id="req-1"),
            Completed(resource_ids=["r-1"]),
            Failed(error="oops"),
        ]
        results = []
        for outcome in outcomes:
            match outcome:
                case Accepted(request_id=rid):
                    results.append(f"accepted:{rid}")
                case Completed(resource_ids=ids):
                    results.append(f"completed:{ids}")
                case RequiresFollowUp():
                    results.append("follow_up")
                case Failed(error=msg):
                    results.append(f"failed:{msg}")
                case _ as unreachable:
                    assert_never(unreachable)

        assert results == ["accepted:req-1", "completed:['r-1']", "failed:oops"]


# ---------------------------------------------------------------------------
# _dispatch_single_attempt — outcome attached on success path
# ---------------------------------------------------------------------------


def _make_service() -> ProvisioningOrchestrationService:
    container = MagicMock()
    logger = MagicMock()
    provider_selection_port = MagicMock()
    provider_config_port = MagicMock()
    config_port = MagicMock()
    circuit_breaker_factory = MagicMock()

    config_port.get_request_config.return_value = {
        "dispatch_timeout_seconds": 10.0,
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


def _make_request(count: int = 2):
    req = MagicMock()
    req.request_id = "req-outcome-test"
    req.requested_count = count
    req.metadata = {}
    req.update_metadata = lambda d: req
    return req


def _make_selection_result() -> ProviderSelectionResult:
    return ProviderSelectionResult(
        provider_name="aws_default_us-east-1",
        provider_type="aws",
        selection_reason="test",
        confidence=1.0,
    )


class TestDispatchSingleAttemptOutcome:
    """_dispatch_single_attempt must attach an OperationOutcome to the result."""

    @pytest.mark.asyncio
    async def test_partial_fulfillment_yields_accepted_outcome(self):
        """Fewer instances than requested → Accepted (still processing)."""
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        svc = _make_service()
        provider_result = ProviderResult.success_result(
            data={
                "resource_ids": ["fleet-1"],
                "instances": [{"id": "i-1"}],  # only 1 of 2 requested
                "instance_ids": ["i-1"],
            },
            metadata={},
        )
        svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        svc._container.get.return_value = scheduler

        result = await svc._dispatch_single_attempt(
            MagicMock(template_id="t-1"),
            _make_request(count=2),
            _make_selection_result(),
            count=2,
        )

        assert result.success is True
        assert isinstance(result.outcome, Accepted)
        assert result.is_final is False

    @pytest.mark.asyncio
    async def test_full_fulfillment_yields_completed_outcome(self):
        """All instances returned → Completed."""
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        svc = _make_service()
        provider_result = ProviderResult.success_result(
            data={
                "resource_ids": ["fleet-1"],
                "instances": [{"id": "i-1"}, {"id": "i-2"}],  # 2 of 2
                "instance_ids": ["i-1", "i-2"],
            },
            metadata={"fulfillment_final": True},
        )
        svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        svc._container.get.return_value = scheduler

        result = await svc._dispatch_single_attempt(
            MagicMock(template_id="t-1"),
            _make_request(count=2),
            _make_selection_result(),
            count=2,
        )

        assert result.success is True
        assert isinstance(result.outcome, Completed)
        assert result.is_final is True

    @pytest.mark.asyncio
    async def test_provider_failure_yields_failed_outcome(self):
        """Provider-side error → Failed outcome attached."""
        from orb.providers.base.strategy.provider_strategy import ProviderResult

        svc = _make_service()
        provider_result = ProviderResult.error_result("InsufficientCapacity", "CAPACITY_ERROR")
        svc._provider_selection_port.execute_operation = AsyncMock(return_value=provider_result)

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        svc._container.get.return_value = scheduler

        result = await svc._dispatch_single_attempt(
            MagicMock(template_id="t-1"),
            _make_request(count=2),
            _make_selection_result(),
            count=2,
        )

        assert result.success is False
        assert isinstance(result.outcome, Failed)
        assert result.is_final is True

    @pytest.mark.asyncio
    async def test_timeout_yields_recoverable_failed_outcome(self):
        """Dispatch timeout → Failed(recoverable=True)."""
        import asyncio

        svc = _make_service()

        async def _hang(*_a, **_kw):
            await asyncio.sleep(60)

        svc._provider_selection_port.execute_operation = _hang

        scheduler = MagicMock()
        scheduler.format_template_for_provider.return_value = {}
        svc._container.get.return_value = scheduler

        result = await svc._dispatch_single_attempt(
            MagicMock(template_id="t-1"),
            _make_request(count=2),
            _make_selection_result(),
            count=2,
            dispatch_timeout_seconds=0.05,
        )

        assert result.success is False
        assert isinstance(result.outcome, Failed)
        assert result.outcome.recoverable is True
        assert result.is_final is True
