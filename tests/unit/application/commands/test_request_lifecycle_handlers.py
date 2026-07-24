"""Unit tests for application/commands/request_lifecycle_handlers.py.

The lifecycle handlers route every status write through the injected
FulfilmentStateMachine (the single write authority). These tests inject a
MagicMock state machine whose ``apply`` returns the (mock) request unchanged,
so they exercise the handler orchestration (find → apply → save → publish) and
the OCC retry loops without depending on real aggregate mutation.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from orb.application.commands.request_lifecycle_handlers import (
    CancelRequestHandler,
    CompleteRequestHandler,
    UpdateRequestStatusHandler,
    _apply_status_via_state_machine,
)
from orb.application.dto.commands import (
    CancelRequestCommand,
    CompleteRequestCommand,
    UpdateRequestStatusCommand,
)
from orb.domain.base.exceptions import ConcurrencyError, EntityNotFoundError
from orb.domain.request.request_types import RequestStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(request_id="req-1"):
    r = MagicMock()
    r.request_id = request_id
    return r


def _make_state_machine():
    """State machine whose apply() echoes the request argument back."""
    sm = MagicMock()
    sm.apply.side_effect = lambda request, *a, **k: request
    return sm


def _make_uow_factory(request=None, save_events=None, find_raises=None):
    uow = MagicMock()
    if find_raises is not None:
        uow.requests.find_by_id.side_effect = find_raises
    else:
        uow.requests.find_by_id.return_value = request
    uow.requests.save.return_value = save_events or []

    @contextmanager
    def _create():
        yield uow

    factory = MagicMock()
    factory.create_unit_of_work.side_effect = _create
    return factory


def _make_logger():
    return MagicMock()


def _make_event_publisher():
    return MagicMock()


def _make_error_handler():
    return MagicMock()


def _make_config_port(concurrency_max_retries=3):
    """Config port whose get_request_config() returns the OCC retry limit.

    Mirrors ConfigurationAdapter.get_request_config(), which exposes
    ``concurrency_max_retries`` as a plain dict entry read via .get(...).
    """
    port = MagicMock()
    port.get_request_config.return_value = {
        "concurrency_max_retries": concurrency_max_retries,
    }
    return port


def _make_request_repo(request=None):
    repo = MagicMock()
    repo.find_by_id.return_value = request
    repo.save.return_value = []
    return repo


# ---------------------------------------------------------------------------
# UpdateRequestStatusHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateRequestStatusHandler:
    def _handler(self, request=None, save_events=None, factory=None, config_retries=3):
        return UpdateRequestStatusHandler(
            uow_factory=factory or _make_uow_factory(request=request, save_events=save_events),
            request_repository=_make_request_repo(request),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
            state_machine=_make_state_machine(),
            config_port=_make_config_port(config_retries),
        )

    @pytest.mark.asyncio
    async def test_happy_path_updates_status(self):
        req = _make_request("req-1")
        captured = {}

        @contextmanager
        def _create():
            uow = MagicMock()
            uow.requests.find_by_id.return_value = req
            uow.requests.save.return_value = []
            captured["uow"] = uow
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create
        sm = _make_state_machine()
        h = UpdateRequestStatusHandler(
            uow_factory=factory,
            request_repository=_make_request_repo(req),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
            state_machine=sm,
            config_port=_make_config_port(),
        )
        cmd = UpdateRequestStatusCommand(
            request_id="req-1", status=RequestStatus.COMPLETED, message="done"
        )
        await h.execute_command(cmd)

        # The status write is routed through the state machine...
        sm.apply.assert_called_once()
        # ...and the resulting aggregate is persisted.
        captured["uow"].requests.save.assert_called_once_with(req)

    @pytest.mark.asyncio
    async def test_publishes_events(self):
        req = _make_request()
        publisher = _make_event_publisher()
        factory = _make_uow_factory(request=req, save_events=["evt1", "evt2"])
        h = UpdateRequestStatusHandler(
            uow_factory=factory,
            request_repository=_make_request_repo(req),
            logger=_make_logger(),
            event_publisher=publisher,
            error_handler=_make_error_handler(),
            state_machine=_make_state_machine(),
            config_port=_make_config_port(),
        )
        cmd = UpdateRequestStatusCommand(request_id="req-1", status=RequestStatus.COMPLETED)
        await h.execute_command(cmd)
        assert publisher.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self):
        h = self._handler(request=None)
        cmd = UpdateRequestStatusCommand(request_id="req-missing", status=RequestStatus.COMPLETED)
        with pytest.raises(EntityNotFoundError):
            await h.execute_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_requires_request_id(self):
        h = self._handler()
        cmd = UpdateRequestStatusCommand(request_id="", status=RequestStatus.COMPLETED)
        with pytest.raises(ValueError, match="request_id"):
            await h.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_concurrency_error_retried_and_eventually_raises(self):
        """ConcurrencyError causes retry up to 3 times then re-raises."""
        factory = _make_uow_factory(find_raises=ConcurrencyError("collision"))
        h = self._handler(factory=factory)
        cmd = UpdateRequestStatusCommand(request_id="req-1", status=RequestStatus.COMPLETED)
        with pytest.raises(ConcurrencyError):
            await h.execute_command(cmd)
        # Should have tried 4 times (0, 1, 2, 3)
        assert factory.create_unit_of_work.call_count == 4

    @pytest.mark.asyncio
    async def test_configured_concurrency_retries_are_honored(self):
        """A configured concurrency_max_retries=1 limits the loop to 1 retry
        (2 attempts total) before the ConcurrencyError is re-raised."""
        factory = _make_uow_factory(find_raises=ConcurrencyError("collision"))
        h = self._handler(factory=factory, config_retries=1)
        cmd = UpdateRequestStatusCommand(request_id="req-1", status=RequestStatus.COMPLETED)
        with pytest.raises(ConcurrencyError):
            await h.execute_command(cmd)
        # 1 retry => attempts 0 and 1 => 2 UoW creations.
        assert factory.create_unit_of_work.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrency_error_succeeds_on_retry(self):
        """ConcurrencyError on first attempt, then success on second."""
        req = _make_request("req-1")
        call_count = 0

        factory = MagicMock()

        def _create():
            nonlocal call_count
            uow = MagicMock()
            if call_count == 0:
                uow.requests.find_by_id.side_effect = ConcurrencyError("clash")
            else:
                uow.requests.find_by_id.return_value = req
                uow.requests.save.return_value = []
            call_count += 1

            @contextmanager
            def _ctx():
                yield uow

            return _ctx()

        factory.create_unit_of_work.side_effect = _create

        h = self._handler(factory=factory)
        cmd = UpdateRequestStatusCommand(request_id="req-1", status=RequestStatus.COMPLETED)
        await h.execute_command(cmd)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        factory = _make_uow_factory(find_raises=RuntimeError("db gone"))
        h = self._handler(factory=factory)
        cmd = UpdateRequestStatusCommand(request_id="req-1", status=RequestStatus.COMPLETED)
        with pytest.raises(RuntimeError, match="db gone"):
            await h.execute_command(cmd)

    @pytest.mark.asyncio
    async def test_logs_error_on_entity_not_found(self):
        logger = _make_logger()
        factory = _make_uow_factory(request=None)
        h = UpdateRequestStatusHandler(
            uow_factory=factory,
            request_repository=_make_request_repo(),
            logger=logger,
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
            state_machine=_make_state_machine(),
            config_port=_make_config_port(),
        )
        cmd = UpdateRequestStatusCommand(request_id="req-x", status=RequestStatus.COMPLETED)
        with pytest.raises(EntityNotFoundError):
            await h.execute_command(cmd)
        logger.error.assert_called()


# ---------------------------------------------------------------------------
# CancelRequestHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCancelRequestHandler:
    def _handler(self, request=None, factory=None, config_retries=3):
        return CancelRequestHandler(
            uow_factory=factory or _make_uow_factory(request=request),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
            state_machine=_make_state_machine(),
            config_port=_make_config_port(config_retries),
        )

    @pytest.mark.asyncio
    async def test_happy_path_cancels_request(self):
        req = _make_request("req-1")
        h = self._handler(request=req)
        cmd = CancelRequestCommand(request_id="req-1", reason="test")
        await h.execute_command(cmd)
        assert cmd.cancelled is True
        assert cmd.final_status == RequestStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self):
        h = self._handler(request=None)
        cmd = CancelRequestCommand(request_id="req-missing", reason="test")
        with pytest.raises(EntityNotFoundError):
            await h.execute_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_requires_request_id(self):
        h = self._handler()
        cmd = CancelRequestCommand(request_id="", reason="test")
        with pytest.raises(ValueError, match="request_id"):
            await h.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_publishes_save_events(self):
        req = _make_request("req-1")
        publisher = _make_event_publisher()
        factory = _make_uow_factory(request=req, save_events=["e1"])
        h = CancelRequestHandler(
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=publisher,
            error_handler=_make_error_handler(),
            state_machine=_make_state_machine(),
            config_port=_make_config_port(),
        )
        cmd = CancelRequestCommand(request_id="req-1", reason="no longer needed")
        await h.execute_command(cmd)
        publisher.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_concurrency_error_retried_and_eventually_raises(self):
        factory = _make_uow_factory(find_raises=ConcurrencyError("collision"))
        h = self._handler(factory=factory)
        cmd = CancelRequestCommand(request_id="req-1", reason="test")
        with pytest.raises(ConcurrencyError):
            await h.execute_command(cmd)
        assert factory.create_unit_of_work.call_count == 4

    @pytest.mark.asyncio
    async def test_configured_concurrency_retries_are_honored(self):
        """concurrency_max_retries=1 limits the OCC loop to 2 attempts total."""
        factory = _make_uow_factory(find_raises=ConcurrencyError("collision"))
        h = self._handler(factory=factory, config_retries=1)
        cmd = CancelRequestCommand(request_id="req-1", reason="test")
        with pytest.raises(ConcurrencyError):
            await h.execute_command(cmd)
        assert factory.create_unit_of_work.call_count == 2

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        factory = MagicMock()
        factory.create_unit_of_work.side_effect = RuntimeError("db gone")
        h = self._handler(factory=factory)
        cmd = CancelRequestCommand(request_id="req-1", reason="test")
        with pytest.raises(RuntimeError, match="db gone"):
            await h.execute_command(cmd)

    @pytest.mark.asyncio
    async def test_cancel_terminal_partial_is_idempotent_noop(self):
        """Cancelling a terminal PARTIAL request must NOT raise (main allowed
        it). The real state machine rejects PARTIAL -> CANCELLED, so the handler
        treats it as an idempotent no-op and reports the current state."""
        from orb.domain.request.aggregate import Request
        from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
        from orb.domain.request.request_types import RequestType

        req = Request.create_new_request(RequestType.ACQUIRE, "tmpl", 3, "aws").model_copy(
            update={"status": RequestStatus.PARTIAL}
        )
        factory = _make_uow_factory(request=req)
        h = CancelRequestHandler(
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
            state_machine=FulfilmentStateMachine(grace_period_seconds=3600),
            config_port=_make_config_port(),
        )
        cmd = CancelRequestCommand(request_id="req-1", reason="user cancel")
        # Must not raise InvalidRequestStateError.
        await h.execute_command(cmd)
        assert cmd.cancelled is False
        assert cmd.final_status == RequestStatus.PARTIAL.value


# ---------------------------------------------------------------------------
# CompleteRequestHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompleteRequestHandler:
    def _handler(self, request=None, repo=None, config_retries=3):
        return CompleteRequestHandler(
            request_repository=repo or _make_request_repo(request),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
            state_machine=_make_state_machine(),
            config_port=_make_config_port(config_retries),
        )

    @pytest.mark.asyncio
    async def test_happy_path_completes_request(self):
        req = _make_request("req-1")
        repo = _make_request_repo(req)
        sm = _make_state_machine()
        h = CompleteRequestHandler(
            request_repository=repo,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
            state_machine=sm,
            config_port=_make_config_port(),
        )
        cmd = CompleteRequestCommand(request_id="req-1")
        await h.execute_command(cmd)

        # The completion is routed through the state machine and persisted.
        sm.apply.assert_called_once()
        repo.save.assert_called_once_with(req)

    @pytest.mark.asyncio
    async def test_raises_not_found_when_missing(self):
        h = self._handler(request=None)
        cmd = CompleteRequestCommand(request_id="req-missing")
        with pytest.raises(EntityNotFoundError):
            await h.execute_command(cmd)

    @pytest.mark.asyncio
    async def test_validate_requires_request_id(self):
        h = self._handler()
        cmd = CompleteRequestCommand(request_id="")
        with pytest.raises(ValueError, match="request_id"):
            await h.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_publishes_events(self):
        req = _make_request("req-1")
        publisher = _make_event_publisher()
        repo = _make_request_repo(req)
        repo.save.return_value = ["e1", "e2"]
        h = CompleteRequestHandler(
            request_repository=repo,
            logger=_make_logger(),
            event_publisher=publisher,
            error_handler=_make_error_handler(),
            state_machine=_make_state_machine(),
            config_port=_make_config_port(),
        )
        cmd = CompleteRequestCommand(request_id="req-1")
        await h.execute_command(cmd)
        assert publisher.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrency_error_retried_and_eventually_raises(self):
        repo = MagicMock()
        repo.find_by_id.return_value = _make_request("req-1")
        repo.save.side_effect = ConcurrencyError("collision")
        h = self._handler(repo=repo)
        cmd = CompleteRequestCommand(request_id="req-1")
        with pytest.raises(ConcurrencyError):
            await h.execute_command(cmd)
        assert repo.save.call_count == 4

    @pytest.mark.asyncio
    async def test_configured_concurrency_retries_are_honored(self):
        """concurrency_max_retries=1 limits the OCC loop to 2 save attempts."""
        repo = MagicMock()
        repo.find_by_id.return_value = _make_request("req-1")
        repo.save.side_effect = ConcurrencyError("collision")
        h = self._handler(repo=repo, config_retries=1)
        cmd = CompleteRequestCommand(request_id="req-1")
        with pytest.raises(ConcurrencyError):
            await h.execute_command(cmd)
        assert repo.save.call_count == 2

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        repo = MagicMock()
        repo.find_by_id.side_effect = RuntimeError("repo gone")
        h = self._handler(repo=repo)
        cmd = CompleteRequestCommand(request_id="req-1")
        with pytest.raises(RuntimeError, match="repo gone"):
            await h.execute_command(cmd)

    @pytest.mark.asyncio
    async def test_logs_error_on_entity_not_found(self):
        logger = _make_logger()
        repo = _make_request_repo(None)
        h = CompleteRequestHandler(
            request_repository=repo,
            logger=logger,
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
            state_machine=_make_state_machine(),
            config_port=_make_config_port(),
        )
        cmd = CompleteRequestCommand(request_id="req-x")
        with pytest.raises(EntityNotFoundError):
            await h.execute_command(cmd)
        logger.error.assert_called()


# ---------------------------------------------------------------------------
# _apply_status_via_state_machine — status routing edge cases
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestApplyStatusViaStateMachine:
    """Direct tests of the status→event routing helper against a REAL state
    machine and REAL Request aggregates."""

    def _sm(self):
        from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine

        return FulfilmentStateMachine(grace_period_seconds=3600)

    def _acquire(self, status: RequestStatus):
        from orb.domain.request.aggregate import Request
        from orb.domain.request.value_objects import RequestType

        r = Request.create_new_request(RequestType.ACQUIRE, "tmpl", 3, "aws")
        return r.model_copy(update={"status": status})

    def _return(self, status: RequestStatus):
        from orb.domain.request.aggregate import Request

        r = Request.create_return_request(
            machine_ids=["i-1", "i-2", "i-3"],
            provider_type="aws",
            provider_name="aws-1",
            provider_api="EC2Fleet",
        )
        return r.model_copy(update={"status": status})

    def test_commanded_timeout_is_rejected_not_rewritten_to_failed(self):
        """A commanded TIMEOUT must raise (it is a deadline-only outcome) rather
        than silently becoming FAILED."""
        req = self._acquire(RequestStatus.IN_PROGRESS)
        with pytest.raises(ValueError, match="TIMEOUT"):
            _apply_status_via_state_machine(
                self._sm(), req, RequestStatus.TIMEOUT, "please time out"
            )

    def test_commanded_failed_still_fails(self):
        """FAILED remains a legal commanded status."""
        req = self._acquire(RequestStatus.IN_PROGRESS)
        out = _apply_status_via_state_machine(self._sm(), req, RequestStatus.FAILED, "boom")
        assert out.status == RequestStatus.FAILED

    def test_return_partial_is_terminal_not_holding_state(self):
        """A RETURN request commanded to PARTIAL (the 'N machines skipped' path)
        lands on terminal PARTIAL, not the non-terminal PARTIAL_PENDING holding
        state."""
        req = self._return(RequestStatus.IN_PROGRESS)
        out = _apply_status_via_state_machine(self._sm(), req, RequestStatus.PARTIAL, "2 skipped")
        assert out.status == RequestStatus.PARTIAL
        assert out.status.is_terminal()

    def test_acquire_partial_within_deadline_is_holding_state(self):
        """Regression guard: ACQUIRE PARTIAL within deadline still uses the
        PARTIAL_PENDING holding state."""
        req = self._acquire(RequestStatus.PENDING)
        # START first so deadline_at is stamped and we are within it.
        from datetime import datetime, timezone

        from orb.domain.request.fulfilment_state_machine import FulfilmentEvent

        sm = self._sm()
        req = sm.apply(req, FulfilmentEvent.START, now=datetime.now(timezone.utc))
        out = _apply_status_via_state_machine(sm, req, RequestStatus.PARTIAL, "1/3")
        assert out.status == RequestStatus.PARTIAL_PENDING
