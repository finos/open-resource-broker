"""Global Server-Sent Events (SSE) endpoint.

Provides a single persistent connection that the UI subscribes to once per page
load, receiving push deltas for machines, requests, templates, and heartbeats.

Wire protocol (standard SSE):
    event: <type>
    data: {"json": "..."}

    (blank line terminates each event)

Event types emitted:
    machine.created / machine.updated / machine.deleted
    request.created / request.updated / request.completed / request.failed
    template.created / template.updated / template.deleted
    heartbeat  — every 15 s, data: {"ts": "<ISO>"}

Query parameters:
    ?since=<ISO>   optional – replay events newer than this timestamp (best-effort)
    ?type=<csv>    optional – comma-separated allow-list of event types

Architecture note:
    ORB has a synchronous handler-based EventBus that dispatches DomainEvents to
    pre-registered handler instances.  That bus does not support async fan-out to
    dynamic, per-request subscribers, so we layer a thin in-process pubsub on top:

        SseEventBus
         - global singleton (module-level)
         - each SSE connection gets its own asyncio.Queue
         - the SseEventHandler (registered with ORB's EventBus in
           bootstrap.core_services) awaits SseEventBus.publish() which
           enqueues to all live queues
         - SSE generator drains the queue and yields formatted lines

    Single-worker only: subscribers live in process memory, so events
    emitted in worker A never reach SSE clients on worker B. Run the
    API with --workers 1 if SSE clients are expected, or move pubsub to
    a shared transport (Redis pub/sub etc.) before scaling out.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

try:
    from fastapi import APIRouter, Query, Request
    from fastapi.responses import StreamingResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process pubsub
# ---------------------------------------------------------------------------

_HEARTBEAT_INTERVAL: float = 15.0  # seconds
_QUEUE_MAXSIZE: int = 256  # drop oldest on overflow rather than blocking


class _SseEventBus:
    """Minimal fan-out pubsub for SSE subscribers.

    Thread-safe for the common asyncio single-thread case. Single-process
    only — see module docstring.
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Optional[tuple[str, dict]]]] = []
        # Store recent events for ?since= replay (capped ring-buffer)
        self._history: list[tuple[datetime, str, dict]] = []
        self._history_max: int = 512

    def subscribe(self) -> asyncio.Queue[Optional[tuple[str, dict]]]:
        """Register a new subscriber; returns its dedicated queue."""
        q: asyncio.Queue[Optional[tuple[str, dict]]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Optional[tuple[str, dict]]]) -> None:
        """Remove subscriber. Safe to call even if already removed."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, event_type: str, payload: dict) -> None:
        """Publish an event from async context."""
        ts = datetime.now(timezone.utc)
        self._record(ts, event_type, payload)
        for q in list(self._subscribers):
            if q.full():
                # Drain one stale entry to make room — prefer freshness
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait((event_type, payload))
            except asyncio.QueueFull:
                pass  # subscriber is too slow; skip rather than block

    def history_since(self, since: datetime) -> list[tuple[str, dict]]:
        """Return (event_type, payload) pairs recorded after *since*."""
        return [(et, p) for (ts, et, p) in self._history if ts > since]

    def _record(self, ts: datetime, event_type: str, payload: dict) -> None:
        self._history.append((ts, event_type, payload))
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max :]


sse_event_bus = _SseEventBus()


# ---------------------------------------------------------------------------
# SSE formatting helpers
# ---------------------------------------------------------------------------

def _format_sse(event_type: str, data: dict) -> str:
    """Format a single SSE message block (terminated by double newline)."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _parse_since(since_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp; return None on failure."""
    if not since_str:
        return None
    try:
        dt = datetime.fromisoformat(since_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _allowed(event_type: str, type_filter: Optional[set[str]]) -> bool:
    """Return True if this event_type passes the type filter."""
    if type_filter is None:
        return True
    return event_type in type_filter


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/events", tags=["Events"])

# Module-level Depends to avoid B008 warnings
_SINCE_QUERY = Query(None, description="Replay events newer than this ISO timestamp")
_TYPE_QUERY = Query(None, description="Comma-separated event type filter")


@router.get(
    "/",
    summary="Global Server-Sent Events stream",
    description=(
        "Subscribe once per page load. Receives push deltas for machines, requests, "
        "templates, and a heartbeat every 15 s.  Supports ?since= for replay and "
        "?type= for filtering."
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "text/event-stream",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_events(
    request: Request,
    since: Optional[str] = _SINCE_QUERY,
    type: Optional[str] = _TYPE_QUERY,  # noqa: A002  — FastAPI maps query param name
) -> StreamingResponse:
    """Open an SSE stream for the caller."""
    since_dt = _parse_since(since)
    type_filter: Optional[set[str]] = (
        {t.strip() for t in type.split(",") if t.strip()} if type else None
    )

    async def generator() -> AsyncGenerator[str, None]:
        q = sse_event_bus.subscribe()
        try:
            # Replay historical events if ?since= provided
            if since_dt is not None:
                for event_type, payload in sse_event_bus.history_since(since_dt):
                    if _allowed(event_type, type_filter):
                        yield _format_sse(event_type, payload)

            while True:
                # Interleave real events with heartbeat deadline
                try:
                    item = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
                    if item is None:
                        # Poison-pill: subscriber was asked to close
                        break
                    event_type, payload = item
                    if _allowed(event_type, type_filter):
                        yield _format_sse(event_type, payload)
                except asyncio.TimeoutError:
                    # No real event arrived within the heartbeat window
                    if await request.is_disconnected():
                        break
                    yield _format_sse(
                        "heartbeat",
                        {"ts": datetime.now(timezone.utc).isoformat()},
                    )
        except asyncio.CancelledError:
            pass
        finally:
            sse_event_bus.unsubscribe(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
