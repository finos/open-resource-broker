"""Unit tests for ListTemplatesHandler active_only filter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from orb.application.dto.queries import ListTemplatesQuery
from orb.application.queries.template_query_handlers import ListTemplatesHandler


def _make_template_dto(is_active: bool) -> MagicMock:
    dto = MagicMock()
    dto.is_active = is_active
    return dto


def _make_handler() -> tuple[ListTemplatesHandler, MagicMock]:
    logger = MagicMock()
    error_handler = MagicMock()
    generic_filter_service = MagicMock()
    generic_filter_service.apply_filters = MagicMock(side_effect=lambda items, _: items)

    active_dto = _make_template_dto(is_active=True)
    inactive_dto = _make_template_dto(is_active=False)

    template_manager = MagicMock()
    template_manager.load_templates = AsyncMock(return_value=[active_dto, inactive_dto])

    container = MagicMock()
    container.get = MagicMock(return_value=template_manager)

    handler = ListTemplatesHandler(
        logger=logger,
        error_handler=error_handler,
        container=container,
        generic_filter_service=generic_filter_service,
    )
    return handler, active_dto


@pytest.mark.asyncio
async def test_active_only_true_filters_inactive() -> None:
    handler, active_dto = _make_handler()
    query = ListTemplatesQuery(active_only=True)
    result = await handler.execute_query(query)
    assert result == [active_dto]


@pytest.mark.asyncio
async def test_active_only_false_returns_all() -> None:
    handler, _ = _make_handler()
    # Re-fetch inactive from the same mock setup
    query = ListTemplatesQuery(active_only=False)
    result = await handler.execute_query(query)
    assert len(result) == 2


def test_active_only_default_is_true() -> None:
    query = ListTemplatesQuery()
    assert query.active_only is True
