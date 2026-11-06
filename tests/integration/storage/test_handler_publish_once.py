# ruff: noqa: F811

"""A conflict followed by a successful retry publishes each domain event once.

The four retry loops (status update, cancel, completion, deadline sweep) run on
every backend with one rival write landing between the read and the write.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
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
from orb.domain.request.fulfilment_state_machine import FulfilmentStateMachine
from orb.domain.request.request_types import RequestStatus
from tests.integration.storage.test_handler_conflict_retry import (  # noqa: F401  (fixture)
    _InterferingFactory,
    _new_request,
    make_uow,
)

LOOPS = ["update-status", "cancel", "complete", "deadline-sweep"]


@pytest.fixture(params=LOOPS)
def loop(request, make_uow):  # noqa: F811
    return request.param


def _config() -> MagicMock:
    config = MagicMock()
    config.get_request_config.return_value = {}
    return config


def _run(loop: str, make_uow: Any) -> MagicMock:
    publisher = MagicMock()
    machine = FulfilmentStateMachine(grace_period_seconds=60)
    request = _new_request()
    if loop == "deadline-sweep":
        request = request.model_copy(
            update={
                "status": RequestStatus.IN_PROGRESS,
                "deadline_at": datetime.now(timezone.utc) - timedelta(hours=1),
            }
        )
    elif loop == "complete":
        request = request.model_copy(update={"status": RequestStatus.IN_PROGRESS})
    with make_uow() as uow:
        uow.requests.save(request)
    request_id = str(request.request_id)

    if loop == "update-status":
        handler = UpdateRequestStatusHandler(
            uow_factory=_InterferingFactory(make_uow, request_id),
            request_repository=MagicMock(),
            logger=MagicMock(),
            event_publisher=publisher,
            error_handler=MagicMock(),
            state_machine=machine,
            config_port=_config(),
        )
        command: Any = UpdateRequestStatusCommand(
            request_id=request_id, status=RequestStatus.IN_PROGRESS, message="go"
        )
        asyncio.run(handler.execute_command(command))
    elif loop == "cancel":
        handler = CancelRequestHandler(
            uow_factory=_InterferingFactory(make_uow, request_id),
            logger=MagicMock(),
            event_publisher=publisher,
            error_handler=MagicMock(),
            state_machine=machine,
            config_port=_config(),
        )
        asyncio.run(
            handler.execute_command(CancelRequestCommand(request_id=request_id, reason="x"))
        )
    elif loop == "complete":
        repository = make_uow().requests
        real_find = repository.find_by_id
        calls = {"n": 0}

        def find_then_interfere(rid: str) -> Any:
            loaded = real_find(rid)
            calls["n"] += 1
            if calls["n"] == 1:
                with make_uow() as rival:
                    other = rival.requests.find_by_id(request_id)
                    rival.requests.save(other.add_resource_id("fleet-1"))  # type: ignore[union-attr]
            return loaded

        repository.find_by_id = find_then_interfere  # type: ignore[method-assign]
        handler = CompleteRequestHandler(
            request_repository=repository,
            logger=MagicMock(),
            event_publisher=publisher,
            error_handler=MagicMock(),
            state_machine=machine,
            config_port=_config(),
        )
        asyncio.run(handler.execute_command(CompleteRequestCommand(request_id=request_id)))
    else:
        from orb.application.queries.request_query_handlers import SyncAndGetRequestHandler

        handler = SyncAndGetRequestHandler(
            uow_factory=_SweepFactory(make_uow, request_id),  # type: ignore[arg-type]
            logger=MagicMock(),
            error_handler=MagicMock(),
            provider_registry_service=MagicMock(),
            machine_sync_service=MagicMock(),
            cache_service=MagicMock(),
            event_publisher=publisher,
            state_machine=machine,
        )
        handler.event_publisher = publisher
        handler._state_machine = machine
        with make_uow() as uow:
            stored = uow.requests.find_by_id(request_id)
        asyncio.run(handler._evaluate_deadline_and_persist(stored))
    return publisher


class _SweepFactory:
    """Units of work whose first save is preceded by a rival write."""

    def __init__(self, make_uow: Any, request_id: str) -> None:
        self._make_uow = make_uow
        self._request_id = request_id
        self._rivalled = False

    def create_unit_of_work(self) -> Any:
        unit = self._make_uow()
        real_save = unit.requests.save

        def save_after_rival(request: Any) -> Any:
            if not self._rivalled:
                self._rivalled = True
                with self._make_uow() as rival:
                    other = rival.requests.find_by_id(self._request_id)
                    rival.requests.save(other.add_resource_id("fleet-1"))  # type: ignore[union-attr]
            return real_save(request)

        unit.requests.save = save_after_rival  # type: ignore[method-assign]
        return unit


@pytest.mark.integration
def test_each_event_is_published_once_after_a_conflict_and_a_retry(loop, make_uow):  # noqa: F811
    publisher = _run(loop, make_uow)

    published = [type(call.args[0]).__name__ for call in publisher.publish.call_args_list]
    assert published, "the retried write published nothing"
    assert max(Counter(published).values()) == 1, published
