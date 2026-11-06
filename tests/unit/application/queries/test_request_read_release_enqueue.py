"""Reads only start the background release of requests that owe one."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from orb.application.queries.request_query_handlers import SyncAndGetRequestHandler
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType


def _terminal(stamp: Optional[str], status: RequestStatus = RequestStatus.PARTIAL) -> Request:
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="tpl",
        machine_count=3,
        provider_type="aws",
        provider_name="aws-default",
    )
    metadata = dict(request.metadata)
    if stamp is not None:
        metadata["deadline_release"] = stamp
    return request.model_copy(
        update={
            "status": status,
            "machine_ids": ["i-1"],
            "metadata": metadata,
            "completed_at": datetime.now(timezone.utc),
        }
    )


def _handler(release: Any) -> SyncAndGetRequestHandler:
    handler = object.__new__(SyncAndGetRequestHandler)
    handler._deadline_release = release
    handler.logger = MagicMock()
    return handler


@pytest.mark.asyncio
async def test_a_read_of_a_marked_terminal_request_only_enqueues():
    from orb.application.services.deadline_release_service import DeadlineReleaseService

    release = MagicMock()
    release.enqueue_if_owed.side_effect = DeadlineReleaseService.is_owed
    request = _terminal("pending")

    result = await _handler(release)._evaluate_deadline_and_persist(request)

    assert result is request
    release.enqueue_if_owed.assert_called_once_with(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("stamp", [None, "done", "nothing_to_release"])
async def test_a_read_of_an_unmarked_terminal_request_releases_nothing(stamp):
    from orb.application.services.deadline_release_service import DeadlineReleaseService

    started: list[Request] = []
    release = MagicMock()
    release.enqueue_if_owed.side_effect = lambda r: (
        started.append(r) if DeadlineReleaseService.is_owed(r) else None
    )

    await _handler(release)._evaluate_deadline_and_persist(_terminal(stamp))

    assert started == []
