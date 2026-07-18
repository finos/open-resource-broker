"""Unit tests for application/commands/request_lifecycle_handlers.py."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from orb.application.commands.request_lifecycle_handlers import (
    CancelRequestHandler,
    CompleteRequestHandler,
    UpdateRequestStatusHandler,
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
    r.update_status = MagicMock(return_value=r)
    r.cancel = MagicMock(return_value=r)
    return r


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
    def _handler(self, request=None, save_events=None):
        return UpdateRequestStatusHandler(
            uow_factory=_make_uow_factory(request=request, save_events=save_events),
            request_repository=_make_request_repo(request),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )

    @pytest.mark.asyncio
    async def test_happy_path_updates_status(self):
        req = _make_request("req-1")
        # Capture the UoW so we can verify the aggregate is saved after mutation.
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
        h = UpdateRequestStatusHandler(
            uow_factory=factory,
            request_repository=_make_request_repo(req),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = UpdateRequestStatusCommand(
            request_id="req-1", status=RequestStatus.COMPLETED, message="done"
        )
        await h.execute_command(cmd)

        # The target status and message are applied to the aggregate...
        req.update_status.assert_called_once_with(status=RequestStatus.COMPLETED, message="done")
        # ...and the mutated aggregate is persisted.
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
        h = UpdateRequestStatusHandler(
            uow_factory=factory,
            request_repository=_make_request_repo(),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = UpdateRequestStatusCommand(request_id="req-1", status=RequestStatus.COMPLETED)
        with pytest.raises(ConcurrencyError):
            await h.execute_command(cmd)
        # Should have tried 4 times (0, 1, 2, 3)
        assert factory.create_unit_of_work.call_count == 4

    @pytest.mark.asyncio
    async def test_concurrency_error_succeeds_on_retry(self):
        """ConcurrencyError on first attempt, then success on second."""
        req = _make_request("req-1")
        call_count = 0

        factory = MagicMock()
        publisher = _make_event_publisher()

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

        h = UpdateRequestStatusHandler(
            uow_factory=factory,
            request_repository=_make_request_repo(req),
            logger=_make_logger(),
            event_publisher=publisher,
            error_handler=_make_error_handler(),
        )
        cmd = UpdateRequestStatusCommand(request_id="req-1", status=RequestStatus.COMPLETED)
        await h.execute_command(cmd)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        factory = _make_uow_factory(find_raises=RuntimeError("db gone"))
        h = UpdateRequestStatusHandler(
            uow_factory=factory,
            request_repository=_make_request_repo(),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
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
    def _handler(self, request=None):
        return CancelRequestHandler(
            uow_factory=_make_uow_factory(request=request),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
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
        )
        cmd = CancelRequestCommand(request_id="req-1", reason="no longer needed")
        await h.execute_command(cmd)
        publisher.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        factory = MagicMock()
        factory.create_unit_of_work.side_effect = RuntimeError("db gone")
        h = CancelRequestHandler(
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = CancelRequestCommand(request_id="req-1", reason="test")
        with pytest.raises(RuntimeError, match="db gone"):
            await h.execute_command(cmd)


# ---------------------------------------------------------------------------
# CompleteRequestHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCompleteRequestHandler:
    def _handler(self, request=None):
        repo = _make_request_repo(request)
        return CompleteRequestHandler(
            request_repository=repo,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )

    @pytest.mark.asyncio
    async def test_happy_path_completes_request(self):
        req = _make_request("req-1")
        repo = _make_request_repo(req)
        h = CompleteRequestHandler(
            request_repository=repo,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = CompleteRequestCommand(request_id="req-1")
        await h.execute_command(cmd)

        # The aggregate is transitioned to COMPLETED and the result is persisted.
        req.update_status.assert_called_once_with(RequestStatus.COMPLETED, "Request completed")
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
        )
        cmd = CompleteRequestCommand(request_id="req-1")
        await h.execute_command(cmd)
        assert publisher.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_generic_exception_propagates(self):
        repo = MagicMock()
        repo.find_by_id.side_effect = RuntimeError("repo gone")
        h = CompleteRequestHandler(
            request_repository=repo,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
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
        )
        cmd = CompleteRequestCommand(request_id="req-x")
        with pytest.raises(EntityNotFoundError):
            await h.execute_command(cmd)
        logger.error.assert_called()
