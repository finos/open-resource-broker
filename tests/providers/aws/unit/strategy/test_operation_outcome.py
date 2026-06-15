"""Unit tests for OperationOutcome discriminated union and FollowUpContext types.

Covers:
- All four variants are instantiable and frozen (value-comparable)
- assert_never compiles and covers all branches
- FollowUpContext variants have the correct discriminator tags
- AWSProviderStrategy implements acquire/return_machines/get_status
"""

from typing import assert_never

import pytest

from orb.domain.base.operation_outcome import (
    Accepted,
    Completed,
    Failed,
    OperationOutcome,
    RequiresFollowUp,
)


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOperationOutcomeVariants:
    """All OperationOutcome variants must be frozen and value-comparable."""

    def test_accepted_default_fields(self):
        o = Accepted(request_id="req-1")
        assert o.request_id == "req-1"
        assert o.pending_resource_ids == []
        assert o.metadata == {}

    def test_accepted_with_pending_ids(self):
        o = Accepted(request_id="req-1", pending_resource_ids=["i-1", "i-2"])
        assert o.pending_resource_ids == ["i-1", "i-2"]

    def test_completed_default_fields(self):
        o = Completed()
        assert o.resource_ids == []
        assert o.metadata == {}

    def test_completed_with_ids(self):
        o = Completed(resource_ids=["i-1"])
        assert o.resource_ids == ["i-1"]

    def test_failed_default_fields(self):
        o = Failed(error="something bad")
        assert o.error == "something bad"
        assert o.recoverable is False
        assert o.metadata == {}

    def test_failed_recoverable(self):
        o = Failed(error="throttle", recoverable=True)
        assert o.recoverable is True

    def test_accepted_is_frozen(self):
        o = Accepted(request_id="req-1")
        with pytest.raises((AttributeError, TypeError)):
            o.request_id = "req-2"  # type: ignore[misc]

    def test_completed_is_frozen(self):
        o = Completed(resource_ids=["i-1"])
        with pytest.raises((AttributeError, TypeError)):
            o.resource_ids = []  # type: ignore[misc]

    def test_failed_is_frozen(self):
        o = Failed(error="bad")
        with pytest.raises((AttributeError, TypeError)):
            o.error = "other"  # type: ignore[misc]

    def test_accepted_value_equality(self):
        a = Accepted(request_id="req-1", pending_resource_ids=["i-1"])
        b = Accepted(request_id="req-1", pending_resource_ids=["i-1"])
        assert a == b

    def test_completed_value_equality(self):
        a = Completed(resource_ids=["i-1", "i-2"])
        b = Completed(resource_ids=["i-1", "i-2"])
        assert a == b


@pytest.mark.unit
class TestFollowUpContext:
    """FollowUpContext variants must carry the correct discriminator tags."""

    def test_termination_context_kind(self):
        from orb.application.services.request_follow_up_context import (
            TerminationFollowUpContext,
        )

        ctx = TerminationFollowUpContext(pending_instance_ids=["i-1"])
        assert ctx.follow_up_kind == "termination"
        assert ctx.expected_terminal_state == "terminated"
        assert ctx.pending_instance_ids == ["i-1"]

    def test_deployment_polling_context_kind(self):
        from orb.application.services.request_follow_up_context import (
            DeploymentPollingFollowUpContext,
        )

        ctx = DeploymentPollingFollowUpContext(pending_resource_ids=["fleet-1"])
        assert ctx.follow_up_kind == "deployment_polling"
        assert ctx.expected_terminal_state == "running"
        assert ctx.pending_resource_ids == ["fleet-1"]

    def test_requires_follow_up_wraps_context(self):
        from orb.application.services.request_follow_up_context import (
            TerminationFollowUpContext,
        )

        ctx = TerminationFollowUpContext()
        outcome: OperationOutcome = RequiresFollowUp(context=ctx)
        assert isinstance(outcome, RequiresFollowUp)
        assert outcome.context.follow_up_kind == "termination"

    def test_termination_context_is_frozen(self):
        from orb.application.services.request_follow_up_context import (
            TerminationFollowUpContext,
        )

        ctx = TerminationFollowUpContext()
        with pytest.raises((AttributeError, TypeError)):
            ctx.expected_terminal_state = "stopped"  # type: ignore[misc]


@pytest.mark.unit
class TestAssertNeverExhaustiveness:
    """Exhaustive match over OperationOutcome must not raise and cover all branches."""

    def _classify(self, outcome: OperationOutcome) -> str:
        match outcome:
            case Accepted():
                return "accepted"
            case Completed():
                return "completed"
            case RequiresFollowUp():
                return "requires_follow_up"
            case Failed():
                return "failed"
            case _ as unreachable:
                assert_never(unreachable)

    def test_accepted_classified(self):
        assert self._classify(Accepted(request_id="r")) == "accepted"

    def test_completed_classified(self):
        assert self._classify(Completed()) == "completed"

    def test_failed_classified(self):
        assert self._classify(Failed(error="e")) == "failed"

    def test_requires_follow_up_classified(self):
        from orb.application.services.request_follow_up_context import TerminationFollowUpContext

        ctx = TerminationFollowUpContext()
        assert self._classify(RequiresFollowUp(context=ctx)) == "requires_follow_up"


# ---------------------------------------------------------------------------
# AWSProviderStrategy — interface contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAWSProviderStrategyOutcomeInterface:
    """AWSProviderStrategy must declare acquire / return_machines / get_status."""

    def test_acquire_method_exists(self):
        import inspect

        from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        assert hasattr(AWSProviderStrategy, "acquire")
        assert asyncio_or_coroutine(AWSProviderStrategy.acquire)

    def test_return_machines_method_exists(self):
        from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        assert hasattr(AWSProviderStrategy, "return_machines")
        assert asyncio_or_coroutine(AWSProviderStrategy.return_machines)

    def test_get_status_method_exists(self):
        from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        assert hasattr(AWSProviderStrategy, "get_status")
        assert asyncio_or_coroutine(AWSProviderStrategy.get_status)

    def test_base_strategy_declares_acquire_abstract(self):
        import inspect

        from orb.providers.base.strategy.base_provider_strategy import BaseProviderStrategy

        assert "acquire" in {m for m in dir(BaseProviderStrategy)}
        method = getattr(BaseProviderStrategy, "acquire")
        assert getattr(method, "__isabstractmethod__", False)

    def test_base_strategy_declares_return_machines_abstract(self):
        from orb.providers.base.strategy.base_provider_strategy import BaseProviderStrategy

        method = getattr(BaseProviderStrategy, "return_machines")
        assert getattr(method, "__isabstractmethod__", False)

    def test_base_strategy_declares_get_status_abstract(self):
        from orb.providers.base.strategy.base_provider_strategy import BaseProviderStrategy

        method = getattr(BaseProviderStrategy, "get_status")
        assert getattr(method, "__isabstractmethod__", False)


def asyncio_or_coroutine(fn) -> bool:
    """Return True if fn is an async function."""
    import asyncio
    import inspect

    return asyncio.iscoroutinefunction(fn) or inspect.iscoroutinefunction(fn)
