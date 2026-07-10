"""Metrics event handler — records provisioning metrics on domain events.

Implementation note
-------------------
Emit sites use the OpenTelemetry Meter API directly.  The ``/metrics``
endpoint serves ``generate_latest(REGISTRY)`` which includes all OTel
instruments bridged via ``PrometheusMetricReader``.

Instruments used
----------------
- ``orb.requests.pending`` (UpDownCounter) — incremented on
  ``RequestCreatedEvent``, decremented on BOTH ``RequestCompletedEvent``
  AND ``RequestFailedEvent``.  This lifecycle is preserved exactly.
- ``orb.requests.total`` (Counter) — incremented on
  ``RequestCompletedEvent``.
- ``orb.active.instances`` (UpDownCounter) — set to the absolute machine
  count on ``RequestCompletedEvent``.  Absolute-set semantics are
  preserved by computing the delta from a tracked previous value.
- ``orb.provisioning.duration`` (Histogram, unit ``s``) — wall-clock
  duration from ``RequestCreatedEvent`` to ``RequestCompletedEvent``,
  measured via the ``_request_start_times`` dict.  The dict is kept as the
  intermediary feeding ``Histogram.record()``.
- ``orb.requests.failed.total`` (Counter) — incremented on
  ``RequestFailedEvent``.

Graceful no-op
--------------
When ``opentelemetry-api`` is absent or no ``MeterProvider`` is configured,
``get_meter()`` returns a no-op meter and all ``add()``/``record()`` calls
are free.  The application runs fully without the ``[monitoring]`` extra.
"""

import time
from typing import Optional

from orb.application.events.base.event_handler import EventHandler
from orb.domain.base.events import DomainEvent
from orb.domain.base.ports import LoggingPort


def _get_meter():
    """Return an OTel Meter (or a no-op object when SDK is absent)."""
    try:
        from opentelemetry import metrics as otel_metrics  # type: ignore[import-not-found]

        return otel_metrics.get_meter(__name__)
    except ImportError:
        return _NoOpMeter()


class _NoOpMeter:
    def create_counter(self, *a, **kw):
        return _NoOpInstrument()

    def create_up_down_counter(self, *a, **kw):
        return _NoOpInstrument()

    def create_histogram(self, *a, **kw):
        return _NoOpInstrument()


class _NoOpInstrument:
    def add(self, *a, **kw) -> None:
        pass

    def record(self, *a, **kw) -> None:
        pass


class MetricsEventHandler(EventHandler):
    """
    Subscribes to provisioning domain events and records OTel metrics.

    Handles:
    - RequestCreatedEvent  -> increments ``orb.requests.pending`` gauge
    - RequestCompletedEvent -> increments ``orb.requests.total``, sets
                               ``orb.active.instances`` (absolute),
                               records ``orb.provisioning.duration`` histogram
    - RequestFailedEvent   -> increments ``orb.requests.failed.total``,
                               decrements ``orb.requests.pending``
    """

    def __init__(
        self,
        logger: Optional[LoggingPort] = None,
    ) -> None:
        super().__init__(logger)
        # Track when requests were created so we can record duration on completion.
        # Kept as the intermediary feeding Histogram.record() — see module docstring.
        self._request_start_times: dict[str, float] = {}

        # Track current pending count so absolute-set semantics work for the
        # UpDownCounter (which only has add(), not set()).
        self._active_instances_current: float = 0.0

        meter = _get_meter()

        self._pending_requests = meter.create_up_down_counter(
            "orb.requests.pending",
            description="Number of provisioning requests currently in-flight.",
            unit="1",
        )
        self._requests_total = meter.create_counter(
            "orb.requests.total",
            description="Total number of successfully completed provisioning requests.",
            unit="1",
        )
        self._active_instances = meter.create_up_down_counter(
            "orb.active.instances",
            description=(
                "Current number of active machine instances.  "
                "Absolute-set semantics: the handler maintains a running total "
                "and records deltas into this UpDownCounter."
            ),
            unit="1",
        )
        self._provisioning_duration = meter.create_histogram(
            "orb.provisioning.duration",
            description=(
                "Wall-clock provisioning duration from RequestCreatedEvent to "
                "RequestCompletedEvent."
            ),
            unit="s",
        )
        self._requests_failed_total = meter.create_counter(
            "orb.requests.failed.total",
            description="Total number of failed provisioning requests.",
            unit="1",
        )

    async def process_event(self, event: DomainEvent) -> None:
        """Route event to the appropriate metrics update."""
        event_type = event.event_type

        if event_type == "RequestCreatedEvent":
            self._handle_request_created(event)
        elif event_type == "RequestCompletedEvent":
            self._handle_request_completed(event)
        elif event_type == "RequestFailedEvent":
            self._handle_request_failed(event)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _handle_request_created(self, event: DomainEvent) -> None:
        request_id = getattr(event, "request_id", event.aggregate_id)
        self._request_start_times[request_id] = time.time()
        self._pending_requests.add(1)

    def _handle_request_completed(self, event: DomainEvent) -> None:
        self._requests_total.add(1)

        machine_ids: list[str] = getattr(event, "machine_ids", [])
        new_count = float(len(machine_ids))
        # Absolute-set semantics: compute delta from current tracked value.
        delta = new_count - self._active_instances_current
        self._active_instances_current = new_count
        self._active_instances.add(delta)

        request_id = getattr(event, "request_id", event.aggregate_id)
        start = self._request_start_times.pop(request_id, None)
        if start is not None:
            self._provisioning_duration.record(time.time() - start)

        self._pending_requests.add(-1)

    def _handle_request_failed(self, event: DomainEvent) -> None:
        self._requests_failed_total.add(1)

        request_id = getattr(event, "request_id", event.aggregate_id)
        self._request_start_times.pop(request_id, None)

        self._pending_requests.add(-1)
