"""The one place a REST body is rendered from a catalog operation."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from orb.interface.catalog import OPERATION_CATALOG, Interface
from orb.interface.response_formatting_service import ResponseFormattingService

FORMAT_HEADER = "X-ORB-Scheduler"


def render_rest(
    entry_key: str,
    formatter: ResponseFormattingService,
    result: Any,
    status: int = 200,
) -> JSONResponse:
    """Render ``result`` of catalog operation ``entry_key`` and name the format used.

    The headers are set here, not by the selector dependency: a header set on an
    injected ``Response`` is dropped when the route returns its own response.
    ``Vary`` tells caches that the body depends on the requested format.
    """
    body = OPERATION_CATALOG[entry_key].renderer_for(Interface.REST)(formatter, result).data
    return JSONResponse(
        content=body,
        status_code=status,
        headers={FORMAT_HEADER: formatter.scheduler_type, "Vary": FORMAT_HEADER},
    )
