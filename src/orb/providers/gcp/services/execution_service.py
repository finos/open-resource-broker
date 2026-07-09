"""GCP execution dispatch helpers."""

from __future__ import annotations

from orb.providers.gcp.types import (
    GCPCreateOperationContext,
    GCPCreateOutcome,
    GCPInstanceStatus,
    GCPMutationOperationContext,
    GCPMutationOutcome,
)


class GCPExecutionService:
    """Dispatch typed GCP contexts to the resolved handler methods."""

    @staticmethod
    def execute_create(context: GCPCreateOperationContext) -> GCPCreateOutcome:
        """Execute a create operation through the resolved handler."""
        return context.handler.acquire_hosts(context.request, context.template)

    @staticmethod
    def execute_terminate(context: GCPMutationOperationContext) -> GCPMutationOutcome:
        """Execute a terminate operation through the resolved handler."""
        return context.handler.terminate_hosts(
            resource_ids=context.resource_ids,
            instance_ids=context.instance_ids,
            context=context.handler_context,
        )

    @staticmethod
    def execute_start(context: GCPMutationOperationContext) -> GCPMutationOutcome:
        """Execute a start operation through the resolved handler."""
        return context.handler.start_instances(
            instance_ids=context.instance_ids,
            context=context.handler_context,
        )

    @staticmethod
    def execute_stop(context: GCPMutationOperationContext) -> GCPMutationOutcome:
        """Execute a stop operation through the resolved handler."""
        return context.handler.stop_instances(
            instance_ids=context.instance_ids,
            context=context.handler_context,
        )

    @staticmethod
    def execute_status(context: GCPMutationOperationContext) -> list[GCPInstanceStatus]:
        """Execute a status/read operation through the resolved handler."""
        return context.handler.check_hosts_status(
            resource_ids=context.resource_ids,
            instance_ids=context.instance_ids,
            context=context.handler_context,
        )
