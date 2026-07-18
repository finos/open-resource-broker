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
    ?since=<ISO>        optional – replay events newer than this timestamp (best-effort)
    ?since_seq=<int>    optional – combined with ?since= to enable gap-detection.
                        When the server cannot fully serve the requested range
                        (oldest surviving history entry is newer than since_seq+1),
                        a synthetic sentinel event is emitted first:
                            event: replay_truncated
                            data: {"type": "replay_truncated", "since": <int>, "seq_id": 0}
                        seq_id 0 is reserved for this sentinel and is never issued by
                        the monotonic counter.  Clients receiving this sentinel should
                        treat their local state as stale and perform a full reload.
    ?type=<csv>         optional – comma-separated allow-list of event types

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
import itertools
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

try:
    from fastapi import APIRouter, Depends, Query, Request
    from fastapi.responses import StreamingResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import require_role

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process pubsub
# ---------------------------------------------------------------------------

_HEARTBEAT_INTERVAL: float = 15.0  # seconds
_QUEUE_MAXSIZE: int = 256  # drop oldest on overflow rather than blocking

# Monotonic sequence counter — starts at 1.  seq_id 0 is reserved for the
# replay_truncated sentinel and is never issued by this counter.
_seq_counter = itertools.count(1)


def _drain_one(q: asyncio.Queue) -> bool:
    """Pop one entry from ``q``. Return True on success, False if empty.

    Wraps ``get_nowait`` to absorb the documented QueueEmpty race when
    another task drains between ``full()`` and ``get_nowait()``.
    """
    try:
        q.get_nowait()
    except asyncio.QueueEmpty:
        return False
    return True


class _SseEventBus:
    """Minimal fan-out pubsub for SSE subscribers.

    Thread-safe for the common asyncio single-thread case. Single-process
    only — see module docstring.

    Subscriber set is protected by ``_subscribers_lock`` so concurrent
    subscribe/unsubscribe coroutines cannot corrupt the collection.
    Publish takes a snapshot of the set under the lock and iterates the
    snapshot, allowing new subscribers to join or leave during fan-out.
    Unsubscribe uses ``set.discard`` which is idempotent — no KeyError if
    the subscriber was already removed.

    Every published event carries a monotonic ``seq_id`` (from the
    module-level ``_seq_counter``).  The history deque stores
    ``(ts, event_type, payload, seq_id)`` tuples.  On reconnect the
    caller may pass ``since_seq`` to detect gaps; ``history_since_seq``
    returns a ``replay_truncated`` sentinel when the deque has overflowed
    and the requested range can no longer be served completely.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Optional[tuple[str, dict]]]] = set()
        self._subscribers_lock: asyncio.Lock = asyncio.Lock()
        # Store recent events for ?since= replay (capped ring-buffer backed by deque
        # so append is O(1) and maxlen enforces the cap without manual slicing).
        self._history_max: int = 512
        self._history: deque[tuple[datetime, str, dict, int]] = deque(maxlen=self._history_max)

    async def subscribe(self) -> asyncio.Queue[Optional[tuple[str, dict]]]:
        """Register a new subscriber; returns its dedicated queue."""
        q: asyncio.Queue[Optional[tuple[str, dict]]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        async with self._subscribers_lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Optional[tuple[str, dict]]]) -> None:
        """Remove subscriber. Safe to call even if already removed.

        ``set.discard`` is idempotent — unlike ``list.remove`` it does not
        raise if the element is absent, so the SSE generator's finally
        clause and an explicit disconnect can both run without coordinating.
        Protected by ``_subscribers_lock`` to prevent concurrent mutation.
        """
        async with self._subscribers_lock:
            self._subscribers.discard(q)

    async def publish(self, event_type: str, payload: dict) -> None:
        """Publish an event from async context.

        Assigns the next monotonic seq_id, records to history, then takes
        a snapshot of current subscribers under the lock and fans out to
        each queue.  Iterating the snapshot (not the live set) allows
        subscribe/unsubscribe to proceed concurrently.

        For each subscriber: if the queue is full, drop the event
        rather than blocking the publisher. The freshness-preferring
        drain (``get_nowait`` before ``put_nowait``) only happens when
        the queue has slack, avoiding the QueueEmpty / QueueFull race
        windows entirely.
        """
        seq_id = next(_seq_counter)
        ts = datetime.now(timezone.utc)
        self._record(ts, event_type, payload, seq_id)
        async with self._subscribers_lock:
            snapshot = set(self._subscribers)
        for q in snapshot:
            self._enqueue_for(q, event_type, payload)

    @staticmethod
    def _enqueue_for(
        q: asyncio.Queue[Optional[tuple[str, dict]]],
        event_type: str,
        payload: dict,
    ) -> None:
        """Push (event_type, payload) onto a subscriber's queue.

        Strategy: check ``full()`` first (LBYL). If full, evict the
        oldest entry then enqueue. Both ``get_nowait``+``put_nowait``
        can still race against another task touching the queue; we
        treat any race as "subscriber is too slow" and drop the event.
        """
        if not q.full():
            q.put_nowait((event_type, payload))
            return
        # Queue full — try to make room by evicting the oldest entry.
        evicted = _drain_one(q)
        if evicted and not q.full():
            q.put_nowait((event_type, payload))
            return
        logger.debug("SSE publish: subscriber queue full; dropping %s", event_type)

    def history_since(self, since: datetime) -> list[tuple[str, dict]]:
        """Return (event_type, payload) pairs recorded after *since*."""
        return [(et, p) for (ts, et, p, _seq) in self._history if ts > since]

    def history_since_seq(self, since: datetime, since_seq: int) -> list[tuple[str, dict]]:
        """Return history with gap-detection via ``since_seq``.

        Emits a synthetic ``replay_truncated`` sentinel as the first item
        whenever the caller's ``since_seq`` claim cannot be satisfied by
        whatever the bus holds today.  This covers two distinct cases:

        1. **Deque overflow** — the oldest surviving deque entry has a
           ``seq_id`` greater than ``since_seq + 1``, meaning at least one
           event was evicted between the client's last known position and
           the start of retained history.

        2. **Empty-history restart** — ``since_seq > 0`` but ``_history``
           is empty (e.g. after a server restart).  The bus has no evidence
           that it ever issued ``since_seq``, so the client's claim is
           impossible to verify; treat it as a gap.

        In both cases the sentinel is::

            ("replay_truncated", {"type": "replay_truncated",
                                   "since": since_seq, "seq_id": 0})

        ``seq_id`` 0 is reserved for this sentinel and is never issued by
        the module-level counter.  Clients that receive this event should
        treat their local view as stale and perform a full reload.

        If ``since_seq == 0`` with an empty deque, or if the oldest entry's
        ``seq_id`` is within the contiguous range (``oldest_seq <=
        since_seq + 1``), no sentinel is emitted.
        """
        sentinel: tuple[str, dict] = (
            "replay_truncated",
            {"type": "replay_truncated", "since": since_seq, "seq_id": 0},
        )

        entries = [(ts, et, p, seq) for (ts, et, p, seq) in self._history if ts > since]

        if not self._history:
            # After a restart (or fresh bus): no history at all.  If the
            # caller claims to have seen events (since_seq > 0), we cannot
            # confirm continuity — emit the sentinel.
            if since_seq > 0:
                return [sentinel]
            return []

        # History is non-empty.
        oldest_seq = self._history[0][3]
        newest_seq = self._history[-1][3]

        # Case 1: gap at the tail of history (deque overflow evicted events
        # that the client has not seen yet).
        if oldest_seq > since_seq + 1:
            return [sentinel] + [(et, p) for (_ts, et, p, _seq) in entries]

        # Case 2: the client claims to have seen a seq_id that is *ahead* of
        # anything this bus has ever issued — impossible unless the bus was
        # restarted and the counter reset.  Treat as truncated.
        if since_seq > newest_seq:
            return [sentinel] + [(et, p) for (_ts, et, p, _seq) in entries]

        return [(et, p) for (_ts, et, p, _seq) in entries]

    def _record(self, ts: datetime, event_type: str, payload: dict, seq_id: int = 0) -> None:
        # deque(maxlen=...) discards the oldest entry automatically on overflow.
        self._history.append((ts, event_type, payload, seq_id))


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
_SINCE_SEQ_QUERY = Query(
    None,
    description=(
        "Combined with ?since=: the sequence_id of the last event the client "
        "received. Enables gap-detection; triggers a replay_truncated sentinel "
        "when the server cannot fully serve the requested range."
    ),
)
_TYPE_QUERY = Query(None, description="Comma-separated event type filter")


@router.get(
    "/",
    operation_id="streamEvents",
    summary="Global Server-Sent Events stream",
    description=(
        "Subscribe once per page load. Receives push deltas for machines, requests, "
        "templates, and a heartbeat every 15 s.  Supports ?since= for replay and "
        "?type= for filtering.  Add ?since_seq= alongside ?since= to enable "
        "gap-detection: if history has overflowed a replay_truncated sentinel is "
        "emitted first (seq_id 0)."
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
    since_seq: Optional[int] = _SINCE_SEQ_QUERY,
    type: Optional[str] = _TYPE_QUERY,
    _user=Depends(require_role("viewer")),
) -> StreamingResponse:
    """Open an SSE stream for the caller."""
    since_dt = _parse_since(since)
    type_filter: Optional[set[str]] = (
        {t.strip() for t in type.split(",") if t.strip()} if type else None
    )

    async def generator() -> AsyncGenerator[str, None]:
        q = await sse_event_bus.subscribe()
        try:
            if since_dt is not None:
                if since_seq is not None:
                    history = sse_event_bus.history_since_seq(since_dt, since_seq)
                else:
                    history = sse_event_bus.history_since(since_dt)
                for event_type, payload in history:
                    if _allowed(event_type, type_filter):
                        yield _format_sse(event_type, payload)

            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_INTERVAL)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    yield _format_sse(
                        "heartbeat",
                        {"ts": datetime.now(timezone.utc).isoformat()},
                    )
                    continue
                if item is None:
                    break
                event_type, payload = item
                if _allowed(event_type, type_filter):
                    yield _format_sse(event_type, payload)
        finally:
            await sse_event_bus.unsubscribe(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
