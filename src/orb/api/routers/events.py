"""Global Server-Sent Events (SSE) endpoint.

Provides a single persistent connection that the UI subscribes to once per page
load, receiving push deltas for machines, requests, templates, and heartbeats.

Wire protocol (standard SSE):
    id: <seq_id>        (present for every real event; absent for heartbeat
                         and the replay_truncated sentinel, whose reserved
                         seq_id 0 must never be adopted as a Last-Event-ID)
    event: <type>
    data: {"json": "..."}

    (blank line terminates each event)

    The ``id:`` line carries the monotonic ``seq_id`` so standard SSE clients
    get Last-Event-ID semantics: on reconnect a client re-opens the stream with
    ``?since_seq=<last id>`` and the server replays only events after that id.

Event types emitted:
    machine.created / machine.updated / machine.deleted
    request.created / request.updated / request.completed / request.failed
    template.created / template.updated / template.deleted
    heartbeat  — every 15 s, data: {"ts": "<ISO>"}

Query parameters:
    ?since=<ISO>        optional – replay events newer than this timestamp (best-effort)
    ?since_seq=<int>    optional – the Last-Event-ID (seq_id) of the last event the
                        client received; standard SSE clients resend it on reconnect.
                        Enables gap-detection and works with OR without ?since= (when
                        alone, replay resumes from the oldest retained event after
                        since_seq).  When the server cannot fully serve the requested
                        range (oldest surviving history entry is newer than
                        since_seq+1), a synthetic sentinel event is emitted first:
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
import time
from collections import deque
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

try:
    from fastapi import APIRouter, Depends, Query, Request
    from fastapi.responses import StreamingResponse
except ImportError:
    raise ImportError("FastAPI routing requires: pip install orb-py[api]") from None

from orb.api.dependencies import require_role
from orb.domain.base.utils import parse_iso_datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-process pubsub
# ---------------------------------------------------------------------------

_HEARTBEAT_INTERVAL: float = 15.0  # seconds
_QUEUE_MAXSIZE: int = 256  # drop oldest on overflow rather than blocking

# Lower bound for "replay everything retained" when a client reconnects with
# ?since_seq= but no ?since= timestamp — every recorded event has ts > _EPOCH.
_EPOCH: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _boot_base() -> int:
    """Return a per-process, monotonic-across-restarts seq_id floor.

    The seq counter must not restart from 1 on every process boot, otherwise a
    client holding a cursor from a previous generation (e.g. since_seq=2) would
    match freshly-issued low ids after a restart and silently skip the genuinely
    new events (the seq 1..5 collision).  Seeding the counter from wall-clock
    microseconds gives every boot a strictly higher floor than the last, so a
    stale cross-restart cursor always lands *below* the new generation's ids and
    is detectable as a gap.

    Microsecond resolution keeps the base (~1.7e15 today) well under JavaScript's
    2**53 safe-integer limit, so the single-integer ``id:`` wire cursor still
    round-trips through every SDK reader unchanged — no wire-format, spec, or
    client change is needed to carry the generation token.
    """
    return int(time.time() * 1_000_000)


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

    Every published event carries a monotonic ``seq_id`` (from this
    instance's ``_seq_counter``, seeded above the previous boot).  The history deque stores
    ``(ts, event_type, payload, seq_id)`` tuples.  On reconnect the
    caller may pass ``since_seq`` to detect gaps; ``history_since_seq``
    returns a ``replay_truncated`` sentinel when the deque has overflowed
    and the requested range can no longer be served completely.
    """

    def __init__(self, boot_base: int = 0) -> None:
        self._subscribers: set[asyncio.Queue[Optional[tuple]]] = set()
        self._subscribers_lock: asyncio.Lock = asyncio.Lock()
        # Store recent events for ?since= replay (capped ring-buffer backed by deque
        # so append is O(1) and maxlen enforces the cap without manual slicing).
        self._history_max: int = 512
        self._history: deque[tuple[datetime, str, dict, int]] = deque(maxlen=self._history_max)
        # Generation floor: the highest seq_id from a *previous* process boot.
        # seq_ids issued by this instance are all strictly greater than
        # ``_boot_base``, so any ``since_seq`` in ``(0, _boot_base]`` belongs to
        # an earlier generation and must trigger replay_truncated.  Defaults to 0
        # (no floor) so a directly-constructed bus behaves like a fresh counter
        # starting at 1 — the module singleton passes a real boot base.
        self._boot_base: int = boot_base
        # Monotonic sequence counter for this generation.  Starts at
        # ``boot_base + 1`` so ids never collide with a prior boot; seq_id 0 stays
        # reserved for the replay_truncated sentinel and is never issued.
        self._seq_counter = itertools.count(boot_base + 1)

    async def subscribe(self) -> asyncio.Queue[Optional[tuple]]:
        """Register a new subscriber; returns its dedicated queue."""
        q: asyncio.Queue[Optional[tuple]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        async with self._subscribers_lock:
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[Optional[tuple]]) -> None:
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
        seq_id = next(self._seq_counter)
        ts = datetime.now(timezone.utc)
        self._record(ts, event_type, payload, seq_id)
        async with self._subscribers_lock:
            snapshot = set(self._subscribers)
        for q in snapshot:
            self._enqueue_for(q, event_type, payload, seq_id)

    @staticmethod
    def _enqueue_for(
        q: asyncio.Queue[Optional[tuple]],
        event_type: str,
        payload: dict,
        seq_id: int,
    ) -> None:
        """Push ``(event_type, payload, seq_id)`` onto a subscriber's queue.

        The ``seq_id`` rides along so the SSE generator can emit the
        wire-level ``id:`` line (Last-Event-ID) for each live event, giving
        reconnecting clients a cursor to replay from.

        Strategy: check ``full()`` first (LBYL). If full, evict the
        oldest entry then enqueue. Both ``get_nowait``+``put_nowait``
        can still race against another task touching the queue; we
        treat any race as "subscriber is too slow" and drop the event.
        """
        if not q.full():
            q.put_nowait((event_type, payload, seq_id))
            return
        # Queue full — try to make room by evicting the oldest entry.
        evicted = _drain_one(q)
        if evicted and not q.full():
            q.put_nowait((event_type, payload, seq_id))
            return
        logger.debug("SSE publish: subscriber queue full; dropping %s", event_type)

    def history_since(self, since: datetime) -> list[tuple[str, dict]]:
        """Return (event_type, payload) pairs recorded after *since*."""
        return [(et, p) for (et, p, _seq) in self.history_since_with_seq(since)]

    def history_since_with_seq(self, since: datetime) -> list[tuple[str, dict, int]]:
        """Return ``(event_type, payload, seq_id)`` triples recorded after *since*.

        Seq-carrying sibling of ``history_since`` so the SSE generator can
        emit the wire-level ``id:`` line for replayed events, giving a
        reconnecting client the same Last-Event-ID cursor it would have
        received live.
        """
        return [(et, p, seq) for (ts, et, p, seq) in self._history if ts > since]

    def history_since_seq(self, since: datetime, since_seq: int) -> list[tuple[str, dict]]:
        """Return history with gap-detection via ``since_seq``.

        Emits a synthetic ``replay_truncated`` sentinel as the first item
        whenever the caller's ``since_seq`` claim cannot be satisfied by
        whatever the bus holds today.  This covers three distinct cases:

        0. **Cross-restart generation gap** — ``0 < since_seq <= _boot_base``,
           i.e. the cursor was issued by a previous process boot.  This
           instance only issues seq_ids above ``_boot_base``, so such a cursor
           is stale; without this check a low pre-restart cursor would match
           the fresh generation's low ids and skip the events in between.

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
        return [(et, p) for (et, p, _seq) in self.history_since_seq_with_seq(since, since_seq)]

    def history_since_seq_with_seq(
        self, since: datetime, since_seq: int
    ) -> list[tuple[str, dict, int]]:
        """Seq-carrying sibling of ``history_since_seq``.

        Returns ``(event_type, payload, seq_id)`` triples so the SSE
        generator can emit each replayed event's wire-level ``id:`` line.
        The gap-detection logic (deque overflow, empty-history restart,
        client-ahead-of-bus) is identical to ``history_since_seq``; only the
        tuple width differs.  The ``replay_truncated`` sentinel carries
        seq_id 0 — the reserved marker that the generator never emits as an
        ``id:`` so a client can never adopt it as a Last-Event-ID.
        """
        sentinel: tuple[str, dict, int] = (
            "replay_truncated",
            {"type": "replay_truncated", "since": since_seq, "seq_id": 0},
            0,
        )

        # Replay only events strictly after the client's cursor. Constrain by
        # both the ISO ``since`` window (when supplied) and ``since_seq`` so a
        # reconnect resumes exactly where the client left off instead of
        # re-emitting events it already applied.
        entries = [
            (et, p, seq) for (ts, et, p, seq) in self._history if ts > since and seq > since_seq
        ]

        # Case 0 (cross-restart generation gap): the client's cursor belongs to a
        # previous process boot.  This instance only issues seq_ids strictly
        # greater than ``_boot_base``, so any ``0 < since_seq <= _boot_base`` is a
        # stale cursor from an earlier generation.  Without this check a client at
        # since_seq=2 reconnecting after a restart (new events 1..5 in the fresh
        # deque) would match ``seq > 2`` and receive only 3,4,5 — silently
        # skipping the genuinely-new seq 1,2.  Force a full resync instead.
        if 0 < since_seq <= self._boot_base:
            return [sentinel] + entries

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
            return [sentinel] + entries

        # Case 2: the client claims to have seen a seq_id that is *ahead* of
        # anything this bus has ever issued — impossible unless the bus was
        # restarted and the counter reset.  Treat as truncated.
        if since_seq > newest_seq:
            return [sentinel] + entries

        return entries

    def _record(self, ts: datetime, event_type: str, payload: dict, seq_id: int = 0) -> None:
        # deque(maxlen=...) discards the oldest entry automatically on overflow.
        self._history.append((ts, event_type, payload, seq_id))


sse_event_bus = _SseEventBus(boot_base=_boot_base())


# ---------------------------------------------------------------------------
# SSE formatting helpers
# ---------------------------------------------------------------------------


def _format_sse(event_type: str, data: dict, seq_id: Optional[int] = None) -> str:
    """Format a single SSE message block (terminated by double newline).

    When ``seq_id`` is a positive integer an ``id:`` line is prepended so
    standard SSE clients record it as the Last-Event-ID and can reconnect
    with ``?since_seq=<id>``.  ``seq_id`` of ``None`` (heartbeat) or ``0``
    (the reserved ``replay_truncated`` sentinel) emits no ``id:`` line — a
    client must never adopt those as a resume cursor.
    """
    prefix = f"id: {seq_id}\n" if seq_id else ""
    return f"{prefix}event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _parse_since(since_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp; return None on failure."""
    if not since_str:
        return None
    try:
        return parse_iso_datetime(since_str)
    except ValueError:
        return None


# Control-signal event types that always pass the ?type= filter. The
# replay_truncated sentinel tells a reconnecting client its local state is
# stale; suppressing it because the client narrowed ?type= would reintroduce
# the silent-gap bug it exists to close.
_ALWAYS_ALLOWED: frozenset[str] = frozenset({"replay_truncated"})


def _allowed(event_type: str, type_filter: Optional[set[str]]) -> bool:
    """Return True if this event_type passes the type filter.

    The ``replay_truncated`` control sentinel always passes, even under a
    narrow ?type= allow-list, so a filtered client still learns it must
    perform a full reload after a history gap.
    """
    if type_filter is None:
        return True
    if event_type in _ALWAYS_ALLOWED:
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
        "?type= for filtering.  Add ?since_seq= (the Last-Event-ID of the last "
        "event received, sent automatically by standard SSE clients on reconnect) "
        "to enable gap-detection: replay resumes after that id, and if history has "
        "overflowed a replay_truncated sentinel is emitted first (seq_id 0).  Every "
        "real event carries an id: line so clients get Last-Event-ID semantics."
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
            # Reconnect replay. ?since_seq= (Last-Event-ID) drives gap-detection
            # and works with OR without ?since=: a standard SSE client resuming
            # from a Last-Event-ID sends only since_seq, so we fall back to the
            # epoch to replay everything the bus still retains after that id.
            if since_seq is not None:
                history = sse_event_bus.history_since_seq_with_seq(
                    since_dt if since_dt is not None else _EPOCH, since_seq
                )
            elif since_dt is not None:
                history = sse_event_bus.history_since_with_seq(since_dt)
            else:
                history = []
            # Track the highest seq_id replayed from history so the live loop can
            # de-dupe. subscribe() runs BEFORE this history snapshot, so any event
            # published in that window lands in BOTH the history and the live
            # queue; without this cursor it would be emitted twice with the same
            # id:. The reserved sentinel seq_id 0 never advances the cursor.
            max_replayed_seq = 0
            for event_type, payload, seq_id in history:
                if seq_id and seq_id > max_replayed_seq:
                    max_replayed_seq = seq_id
                if _allowed(event_type, type_filter):
                    yield _format_sse(event_type, payload, seq_id)

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
                # Queue items are (event_type, payload, seq_id). Tolerate legacy
                # 2-tuples (event_type, payload) so any external enqueuer or test
                # that predates the seq_id contract still streams (id: omitted).
                event_type, payload = item[0], item[1]
                seq_id = item[2] if len(item) > 2 else None
                # De-dupe the history/live overlap: skip any live item whose
                # seq_id was already replayed from history. Only real events carry
                # a seq_id; legacy 2-tuples (seq_id=None) always pass through.
                if seq_id is not None and seq_id <= max_replayed_seq:
                    continue
                if _allowed(event_type, type_filter):
                    yield _format_sse(event_type, payload, seq_id)
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
