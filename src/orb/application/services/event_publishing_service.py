"""Event publishing service for centralizing event publishing logic."""

from orb.domain.base.events import RequestCompletedEvent, RequestCreatedEvent
from orb.domain.base.ports.event_publisher_port import EventPublisherPort
from orb.domain.request.aggregate import Request


class EventPublishingService:
    """Centralized event publishing service."""

    def __init__(self, event_publisher: EventPublisherPort):
        self._event_publisher = event_publisher

    def publish_request_created(self, request: Request) -> None:
        """Publish request created event."""
        request_id_str = str(request.request_id)
        event = RequestCreatedEvent(
            aggregate_id=request_id_str,
            aggregate_type="Request",
            request_id=request_id_str,
            request_type=request.request_type.value,
            template_id=request.template_id,
            machine_count=request.requested_count,
        )
        self._event_publisher.publish(event)

    def publish_request_completed(self, request: Request) -> None:
        """Publish request completed event."""
        request_id_str = str(request.request_id)
        event = RequestCompletedEvent(
            aggregate_id=request_id_str,
            aggregate_type="Request",
            request_id=request_id_str,
            request_type=request.request_type.value,
            completion_status=request.status.value,
            machine_ids=request.machine_ids,
        )
        self._event_publisher.publish(event)
