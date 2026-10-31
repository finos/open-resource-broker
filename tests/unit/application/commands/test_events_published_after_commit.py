"""Domain events are published only once the unit of work has committed."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.commands.request_creation_handlers import CreateReturnRequestHandler
from orb.application.commands.request_lifecycle_handlers import (
    CancelRequestHandler,
    UpdateRequestStatusHandler,
)
from orb.application.dto.commands import CancelRequestCommand, UpdateRequestStatusCommand
from orb.application.queries.request_query_handlers import SyncAndGetRequestHandler


class _CommitProbe:
    """Unit-of-work factory that records whether the commit (``with`` exit) happened."""

    def __init__(self, *, commit_fails: bool = False, events: list | None = None) -> None:
        self.committed = False
        self.commit_fails = commit_fails
        self.uow = MagicMock()
        self.uow.requests.save.return_value = events if events is not None else [MagicMock()]
        self.uow.machines.get_by_id.return_value = None
        self.factory = MagicMock()
        self.factory.create_unit_of_work.side_effect = self._create

    @contextmanager
    def _create(self):
        yield self.uow
        if self.commit_fails:
            raise RuntimeError("commit failed")
        self.committed = True


def _publisher_recording(probe: _CommitProbe) -> MagicMock:
    publisher = MagicMock()
    publisher.published_after_commit = []
    publisher.publish.side_effect = lambda event: publisher.published_after_commit.append(
        probe.committed
    )
    return publisher


def _lifecycle_kwargs(probe, publisher):
    sm = MagicMock()
    sm.apply.side_effect = lambda request, *a, **k: request
    cfg = MagicMock()
    cfg.get_request_config.return_value = {"concurrency_max_retries": 0}
    return dict(
        uow_factory=probe.factory,
        logger=MagicMock(),
        event_publisher=publisher,
        error_handler=MagicMock(),
        state_machine=sm,
        config_port=cfg,
    )


@pytest.mark.unit
class TestEventsPublishedAfterCommit:
    @pytest.mark.asyncio
    async def test_status_update_publishes_after_commit(self):
        probe = _CommitProbe()
        publisher = _publisher_recording(probe)
        probe.uow.requests.find_by_id.return_value = MagicMock()
        handler = UpdateRequestStatusHandler(
            request_repository=MagicMock(), **_lifecycle_kwargs(probe, publisher)
        )
        await handler.execute_command(
            UpdateRequestStatusCommand(request_id="r-1", status="in_progress", message="m")
        )
        assert publisher.published_after_commit == [True]

    @pytest.mark.asyncio
    async def test_status_update_does_not_publish_when_commit_fails(self):
        probe = _CommitProbe(commit_fails=True)
        publisher = _publisher_recording(probe)
        probe.uow.requests.find_by_id.return_value = MagicMock()
        handler = UpdateRequestStatusHandler(
            request_repository=MagicMock(), **_lifecycle_kwargs(probe, publisher)
        )
        with pytest.raises(RuntimeError):
            await handler.execute_command(
                UpdateRequestStatusCommand(request_id="r-1", status="in_progress", message="m")
            )
        publisher.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_publishes_after_commit_only(self):
        probe = _CommitProbe()
        publisher = _publisher_recording(probe)
        probe.uow.requests.find_by_id.return_value = MagicMock()
        handler = CancelRequestHandler(**_lifecycle_kwargs(probe, publisher))
        await handler.execute_command(CancelRequestCommand(request_id="r-1", reason="x"))
        assert publisher.published_after_commit == [True]

        failing = _CommitProbe(commit_fails=True)
        publisher = _publisher_recording(failing)
        failing.uow.requests.find_by_id.return_value = MagicMock()
        handler = CancelRequestHandler(**_lifecycle_kwargs(failing, publisher))
        with pytest.raises(RuntimeError):
            await handler.execute_command(CancelRequestCommand(request_id="r-1", reason="x"))
        publisher.publish.assert_not_called()

    def test_return_request_publishes_after_commit_only(self):
        for fails in (False, True):
            probe = _CommitProbe(commit_fails=fails)
            publisher = _publisher_recording(probe)
            handler = object.__new__(CreateReturnRequestHandler)
            handler.logger = MagicMock()
            handler.event_publisher = publisher
            handler.uow_factory = probe.factory
            request = MagicMock()
            request.request_id = "ret-1"
            if fails:
                with pytest.raises(RuntimeError):
                    handler._cancel_validate_and_persist(["m-1"], request, force_return=False)
                publisher.publish.assert_not_called()
            else:
                handler._cancel_validate_and_persist(["m-1"], request, force_return=False)
                assert publisher.published_after_commit == [True]

    @pytest.mark.asyncio
    async def test_deadline_sweep_publishes_after_commit_and_not_on_failed_commit(self):
        for fails in (False, True):
            probe = _CommitProbe(commit_fails=fails)
            publisher = _publisher_recording(probe)
            handler = object.__new__(SyncAndGetRequestHandler)
            handler.logger = MagicMock()
            handler.event_publisher = publisher
            handler.uow_factory = probe.factory
            handler._query_service = AsyncMock()
            swept = MagicMock()
            request = MagicMock()
            request.status.is_terminal.return_value = False
            handler._state_machine = MagicMock()
            handler._state_machine.evaluate_deadline.return_value = swept
            swept.status = "timeout"
            request.status.__eq__ = lambda self, other: False  # type: ignore[assignment]
            await handler._evaluate_deadline_and_persist(request)
            if fails:
                publisher.publish.assert_not_called()
            else:
                assert publisher.published_after_commit == [True]
