"""Helpers for durable provider follow-up context stored on requests."""

from __future__ import annotations

from typing import Any

from orb.domain.base.follow_up_context import (
    DeploymentPollingFollowUpContext,
    FollowUpContext,
    TerminationFollowUpContext,
)
from orb.domain.request.aggregate import Request

FOLLOW_UP_CONTEXT_KEY = "follow_up_context"

__all__ = [
    "DeploymentPollingFollowUpContext",
    "FOLLOW_UP_CONTEXT_KEY",
    "FollowUpContext",
    "TerminationFollowUpContext",
    "get_request_follow_up_context",
    "merge_request_metadata_with_follow_up_context",
    "with_request_follow_up_context",
]


def get_request_follow_up_context(request: Request) -> dict[str, Any]:
    """Return durable provider follow-up context for a request."""
    follow_up_context = request.provider_data.get(FOLLOW_UP_CONTEXT_KEY)
    if not isinstance(follow_up_context, dict):
        return {}

    return dict(follow_up_context)


def merge_request_metadata_with_follow_up_context(request: Request) -> dict[str, Any]:
    """Merge request metadata with durable provider follow-up context."""
    request_metadata = dict(request.metadata)
    request_metadata = {**get_request_follow_up_context(request), **request_metadata}
    return request_metadata


def with_request_follow_up_context(request: Request, updates: dict[str, Any]) -> Request:
    """Return a request with merged durable provider follow-up context."""
    if not updates:
        return request

    request_provider_data = dict(request.provider_data)
    follow_up_context = get_request_follow_up_context(request)
    for key, value in updates.items():
        if value not in (None, ""):
            follow_up_context[key] = value

    request_provider_data[FOLLOW_UP_CONTEXT_KEY] = follow_up_context
    return request.set_provider_data(request_provider_data)
