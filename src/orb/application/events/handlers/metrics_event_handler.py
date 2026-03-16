"""Metrics event handler — records provisioning metrics on domain events."""

import time
from typing import Optional

from orb.application.events.base.event_handler import EventHandler
from orb.domain.base.events import DomainEvent
from orb.domain.base.ports import LoggingPort
from orb.monitoring.metrics import MetricsCollector


class MetricsEventHandler(EventHandler):
    """
    Subscribes to provisioning domain events and updates MetricsCollector.

    Handles:
    - RequestCreatedEvent  -> increments pending_requests gauge
    - RequestCompletedEvent -> increments requests_total, sets active_instances,
                               records provisioning_duration
    - RequestFailedEvent   -> increments requests_failed_total
    """

    def __init__(
        self,
        collector: MetricsCollector,
        logger: Optional[LoggingPort] = None,
    ) -> None:
        super().__init__(logger)
        self._collector = collector
        # Track when requests were created so we can record duration on completion
        self._request_start_times: dict[str, float] = {}

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
        self._collector.increment_gauge("pending_requests")

    def _handle_request_completed(self, event: DomainEvent) -> None:
        self._collector.increment_counter("requests_total")

        machine_ids: list[str] = getattr(event, "machine_ids", [])
        self._collector.set_gauge("active_instances", float(len(machine_ids)))

        request_id = getattr(event, "request_id", event.aggregate_id)
        start = self._request_start_times.pop(request_id, None)
        if start is not None:
            self._collector.record_time("provisioning_duration", time.time() - start)

        self._collector.decrement_gauge("pending_requests")

    def _handle_request_failed(self, event: DomainEvent) -> None:
        self._collector.increment_counter("requests_failed_total")

        request_id = getattr(event, "request_id", event.aggregate_id)
        self._request_start_times.pop(request_id, None)

        self._collector.decrement_gauge("pending_requests")
