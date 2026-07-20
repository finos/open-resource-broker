"""Unit tests for application/commands/cleanup_handlers.py."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from orb.application.commands.cleanup_handlers import (
    CleanupAllResourcesHandler,
    CleanupOldRequestsHandler,
)
from orb.application.dto.commands import (
    CleanupAllResourcesCommand,
    CleanupOldRequestsCommand,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(request_id="req-1"):
    r = MagicMock()
    r.request_id = request_id
    return r


def _make_machine(machine_id="m-1"):
    m = MagicMock()
    m.machine_id = machine_id
    return m


def _make_uow_factory(
    old_requests=None,
    old_machines=None,
    delete_request_error=False,
    delete_machine_error=False,
):
    uow = MagicMock()
    uow.requests.find_old_requests.return_value = old_requests or []
    uow.machines.find_old_machines.return_value = old_machines or []

    if delete_request_error:
        uow.requests.delete.side_effect = RuntimeError("delete req failed")
    if delete_machine_error:
        uow.machines.delete.side_effect = RuntimeError("delete machine failed")

    uow.commit = MagicMock()

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


def _make_request_repo():
    return MagicMock()


def _make_machine_repo():
    return MagicMock()


# ---------------------------------------------------------------------------
# CleanupOldRequestsHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupOldRequestsHandler:
    def _handler(self, old_requests=None, delete_error=False):
        return CleanupOldRequestsHandler(
            request_repository=_make_request_repo(),
            uow_factory=_make_uow_factory(
                old_requests=old_requests, delete_request_error=delete_error
            ),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self):
        requests = [_make_request("req-1"), _make_request("req-2")]
        h = self._handler(old_requests=requests)
        cmd = CleanupOldRequestsCommand(older_than_days=30, dry_run=True)
        await h.execute_command(cmd)
        assert cmd.requests_cleaned == 0
        assert cmd.request_ids_found is not None
        assert len(cmd.request_ids_found) == 2

    @pytest.mark.asyncio
    async def test_dry_run_returns_found_ids(self):
        requests = [_make_request("req-abc")]
        h = self._handler(old_requests=requests)
        cmd = CleanupOldRequestsCommand(older_than_days=30, dry_run=True)
        await h.execute_command(cmd)
        assert cmd.request_ids_found is not None
        assert "req-abc" in cmd.request_ids_found

    @pytest.mark.asyncio
    async def test_actual_cleanup_deletes_requests(self):
        requests = [_make_request("req-1"), _make_request("req-2")]
        factory = _make_uow_factory(old_requests=requests)
        h = CleanupOldRequestsHandler(
            request_repository=_make_request_repo(),
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = CleanupOldRequestsCommand(older_than_days=30, dry_run=False)
        await h.execute_command(cmd)
        assert cmd.requests_cleaned == 2

    @pytest.mark.asyncio
    async def test_cleanup_commits_transaction(self):
        commit_called = []
        req = _make_request()

        @contextmanager
        def _create():
            uow = MagicMock()
            uow.requests.find_old_requests.return_value = [req]
            uow.commit.side_effect = lambda: commit_called.append(True)
            yield uow

        factory = MagicMock()
        factory.create_unit_of_work.side_effect = _create
        h = CleanupOldRequestsHandler(
            request_repository=_make_request_repo(),
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = CleanupOldRequestsCommand(older_than_days=30, dry_run=False)
        await h.execute_command(cmd)
        assert commit_called, "uow.commit() should have been called"

    @pytest.mark.asyncio
    async def test_cleanup_publishes_event(self):
        publisher = _make_event_publisher()
        factory = _make_uow_factory(old_requests=[_make_request("req-1"), _make_request("req-2")])
        h = CleanupOldRequestsHandler(
            request_repository=_make_request_repo(),
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=publisher,
            error_handler=_make_error_handler(),
        )
        cmd = CleanupOldRequestsCommand(older_than_days=30, dry_run=False)
        await h.execute_command(cmd)
        publisher.publish.assert_called_once()
        (event,) = publisher.publish.call_args.args
        # Event must carry the actual number of requests cleaned and reference the age threshold.
        assert event.resource_count == 2
        assert event.resource_count == cmd.requests_cleaned
        assert event.resource_type == "Request"
        assert event.cleanup_reason == "Cleanup requests older than 30 days"

    @pytest.mark.asyncio
    async def test_per_item_delete_error_continues(self):
        requests = [_make_request("req-1"), _make_request("req-2")]
        logger = MagicMock()
        factory = _make_uow_factory(old_requests=requests, delete_request_error=True)
        h2 = CleanupOldRequestsHandler(
            request_repository=_make_request_repo(),
            uow_factory=factory,
            logger=logger,
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = CleanupOldRequestsCommand(older_than_days=30, dry_run=False)
        await h2.execute_command(cmd)
        # Errors logged per item but execution continues
        assert logger.error.call_count >= 1
        assert cmd.requests_cleaned == 0  # all failed

    @pytest.mark.asyncio
    async def test_validate_command_raises_on_invalid_days(self):
        h = self._handler()
        cmd = CleanupOldRequestsCommand(older_than_days=0)
        with pytest.raises(ValueError, match="positive"):
            await h.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_uow_exception_propagates(self):
        factory = MagicMock()
        factory.create_unit_of_work.side_effect = RuntimeError("db error")
        h = CleanupOldRequestsHandler(
            request_repository=_make_request_repo(),
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = CleanupOldRequestsCommand(older_than_days=7, dry_run=False)
        with pytest.raises(RuntimeError, match="db error"):
            await h.execute_command(cmd)


# ---------------------------------------------------------------------------
# CleanupAllResourcesHandler
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCleanupAllResourcesHandler:
    def _handler(self, old_requests=None, old_machines=None):
        return CleanupAllResourcesHandler(
            request_repository=_make_request_repo(),
            machine_repository=_make_machine_repo(),
            uow_factory=_make_uow_factory(old_requests=old_requests, old_machines=old_machines),
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )

    @pytest.mark.asyncio
    async def test_dry_run_does_not_delete(self):
        h = self._handler(
            old_requests=[_make_request(), _make_request()],
            old_machines=[_make_machine()],
        )
        cmd = CleanupAllResourcesCommand(older_than_days=30, dry_run=True)
        await h.execute_command(cmd)
        assert cmd.requests_cleaned == 0
        assert cmd.machines_cleaned == 0
        assert cmd.total_cleaned == 0

    @pytest.mark.asyncio
    async def test_actual_cleanup_counts_resources(self):
        factory = _make_uow_factory(
            old_requests=[_make_request("r1"), _make_request("r2")],
            old_machines=[_make_machine("m1"), _make_machine("m2"), _make_machine("m3")],
        )
        publisher = _make_event_publisher()
        h = CleanupAllResourcesHandler(
            request_repository=_make_request_repo(),
            machine_repository=_make_machine_repo(),
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=publisher,
            error_handler=_make_error_handler(),
        )
        cmd = CleanupAllResourcesCommand(older_than_days=30, dry_run=False)
        await h.execute_command(cmd)
        assert cmd.requests_cleaned == 2
        assert cmd.machines_cleaned == 3
        assert cmd.total_cleaned == 5

    @pytest.mark.asyncio
    async def test_actual_cleanup_publishes_event(self):
        factory = _make_uow_factory(
            old_requests=[_make_request("r1"), _make_request("r2")],
            old_machines=[_make_machine("m1")],
        )
        publisher = _make_event_publisher()
        h = CleanupAllResourcesHandler(
            request_repository=_make_request_repo(),
            machine_repository=_make_machine_repo(),
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=publisher,
            error_handler=_make_error_handler(),
        )
        cmd = CleanupAllResourcesCommand(older_than_days=30, dry_run=False)
        await h.execute_command(cmd)
        publisher.publish.assert_called_once()
        (event,) = publisher.publish.call_args.args
        # resource_count must equal requests + machines cleaned (2 + 1 = 3).
        assert event.resource_count == 3
        assert event.resource_count == cmd.total_cleaned
        assert event.resource_type == "Multiple"
        assert event.cleanup_reason == "Cleanup all resources older than 30 days"

    @pytest.mark.asyncio
    async def test_validate_command_raises_on_invalid_days(self):
        h = self._handler()
        cmd = CleanupAllResourcesCommand(older_than_days=-5)
        with pytest.raises(ValueError, match="positive"):
            await h.validate_command(cmd)

    @pytest.mark.asyncio
    async def test_per_item_machine_delete_error_continues(self):
        factory = _make_uow_factory(
            old_requests=[],
            old_machines=[_make_machine("m1"), _make_machine("m2")],
            delete_machine_error=True,
        )
        logger = _make_logger()
        h = CleanupAllResourcesHandler(
            request_repository=_make_request_repo(),
            machine_repository=_make_machine_repo(),
            uow_factory=factory,
            logger=logger,
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = CleanupAllResourcesCommand(older_than_days=30, dry_run=False)
        await h.execute_command(cmd)
        assert logger.error.call_count >= 1
        assert cmd.machines_cleaned == 0

    @pytest.mark.asyncio
    async def test_uow_exception_propagates(self):
        factory = MagicMock()
        factory.create_unit_of_work.side_effect = RuntimeError("db gone")
        h = CleanupAllResourcesHandler(
            request_repository=_make_request_repo(),
            machine_repository=_make_machine_repo(),
            uow_factory=factory,
            logger=_make_logger(),
            event_publisher=_make_event_publisher(),
            error_handler=_make_error_handler(),
        )
        cmd = CleanupAllResourcesCommand(older_than_days=30, dry_run=False)
        with pytest.raises(RuntimeError, match="db gone"):
            await h.execute_command(cmd)
