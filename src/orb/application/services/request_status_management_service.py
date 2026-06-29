"""Service for managing request status updates and persistence."""

from typing import Any, Dict, List, cast

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import LoggingPort
from orb.domain.machine.aggregate import Machine

from .provisioning_orchestration_service import ProvisioningResult


class RequestStatusManagementService:
    """Service for managing request status updates and persistence."""

    def __init__(self, uow_factory: UnitOfWorkFactory, logger: LoggingPort):
        self._uow_factory = uow_factory
        self._logger = logger

    async def update_request_from_provisioning(
        self, request: Any, provisioning_result: ProvisioningResult
    ) -> Any:
        """Update request status and data from provisioning results."""

        if not provisioning_result.success:
            return self._handle_provisioning_failure(request, provisioning_result)

        # Store resource IDs and provider metadata
        resource_ids = provisioning_result.resource_ids
        instances = provisioning_result.instances
        provider_data = provisioning_result.provider_data

        self._logger.info(
            "Processing provisioning success: %d resources, %d instances",
            len(resource_ids),
            len(instances),
        )

        # Store provider API in domain field
        # Provider API already set by RequestCreationService

        # Add resource IDs to request
        for resource_id in resource_ids:
            if isinstance(resource_id, str):
                request = request.add_resource_id(resource_id)

        # Populate machine IDs if available
        instance_ids = self._extract_machine_ids(provisioning_result)
        if instance_ids:
            request = request.add_machine_ids(instance_ids)
            self._logger.info("Populated %d machine IDs immediately", len(instance_ids))

        # Store provider-specific data
        if provider_data:
            request = request.set_provider_data({**request.provider_data, **provider_data})

        # Handle provider errors for partial success
        provider_errors = (
            provider_data.get("fleet_errors", []) if isinstance(provider_data, dict) else []
        )
        has_api_errors = bool(provider_errors)

        if has_api_errors and not request.metadata.get("fleet_errors"):
            request = request.update_metadata({"fleet_errors": provider_errors})

        # Create and save machine aggregates
        if instances:
            machines_to_save = []
            for instance_data in instances:
                machine = self._create_machine_aggregate(
                    instance_data, request, request.template_id
                )
                machines_to_save.append(machine)

            if machines_to_save:
                with self._uow_factory.create_unit_of_work() as uow:
                    uow.machines.save_batch(machines_to_save)

        # Derive fulfillment finality from ProvisioningResult.is_final, which
        # is itself derived from the typed OperationOutcome by
        # ProvisioningResult.__post_init__:
        #   Completed         → is_final=True   (instances reached final state)
        #   Accepted          → is_final=False  (provider accepted; pending)
        #   RequiresFollowUp  → is_final=False  (async follow-up needed)
        #   Failed            → is_final=True   (failure path handled separately)
        #
        # This is the single source of truth across all providers (AWS, future
        # Azure/GCP/K8s). Per-handler fulfillment_final flags inside
        # provider_data are no longer consulted here — providers express
        # finality via OperationOutcome.
        return self._update_request_status(
            request,
            len(instances),
            request.requested_count,
            has_api_errors,
            provider_errors,
            fulfillment_final=provisioning_result.is_final,
        )

    def _handle_provisioning_failure(self, request: Any, provisioning_result: Any) -> Any:
        """Handle provisioning failure, capturing provider error details when available."""
        from orb.domain.request.value_objects import RequestStatus

        error_message = (
            provisioning_result.error_message or "Provisioning failed (no error details)"
        )
        request = request.update_status(
            RequestStatus.FAILED, f"Provisioning failed: {error_message}"
        )

        request = request.update_metadata(
            {"error_message": error_message, "error_type": "ProvisioningFailure"}
        )

        # Persist structured provider error details so they are available to the status
        # response.  Only non-None fields are included to keep error_details clean.
        provider_error_code: str | None = getattr(provisioning_result, "provider_error_code", None)
        provider_error_message: str | None = getattr(
            provisioning_result, "provider_error_message", None
        )
        provider_request_id: str | None = getattr(provisioning_result, "provider_request_id", None)
        error_source: str | None = getattr(provisioning_result, "error_source", None)

        if any([provider_error_code, provider_error_message, provider_request_id, error_source]):
            aws_error_block: dict[str, Any] = {}
            if provider_error_code:
                aws_error_block["code"] = provider_error_code
            if provider_error_message:
                aws_error_block["message"] = provider_error_message
            if error_source:
                aws_error_block["source"] = error_source
            if provider_request_id:
                aws_error_block["aws_request_id"] = provider_request_id

            # Merge into error_details so it survives serialization / persistence.
            current = dict(request.error_details) if request.error_details else {}
            current["provider_error"] = aws_error_block
            # Pydantic freeze-safe: use model_copy for the error_details field.
            from orb.domain.request.aggregate import Request as RequestAggregate

            if isinstance(request, RequestAggregate):
                fields = request.model_dump()
                fields["error_details"] = current
                fields["version"] = request.version + 1
                request = RequestAggregate.model_validate(fields)
            else:
                # Fallback for mock objects in tests
                request.error_details = current  # type: ignore[attr-defined]

        return request

    def _update_request_status(
        self,
        request: Any,
        instance_count: int,
        requested_count: int,
        has_api_errors: bool,
        provider_errors: List[Dict],
        fulfillment_final: bool = True,
    ) -> Any:
        """Update request status based on fulfillment and errors.

        ``instance_count`` is the authoritative count of instances the
        provider just confirmed as fulfilled (derived from
        ``len(ProvisioningResult.instances)`` by the caller). It is
        written to ``request.successful_count`` whenever the count is
        non-zero so the persisted counter matches reality.

        The aggregate's ``update_status`` only touches status / message
        / completed_at — it does not bump ``successful_count``. The
        legacy counter-update path
        (``Request.update_with_provisioning_result``) only fires when
        the provider emits a top-level ``instance_ids`` key, which the
        EC2Fleet instant path does not. Doing the bump here keeps the
        wire payload consistent across both batched-instance and
        instant-fulfilment providers.
        """
        from orb.domain.request.value_objects import RequestStatus

        error_summary = None
        if has_api_errors:
            error_summary = (
                "; ".join(
                    f"{err.get('error_code', 'Unknown')}: {err.get('error_message', 'No message')}"
                    for err in provider_errors
                )
                or "Unknown API errors"
            )

        # Reconcile the persisted ``successful_count`` against the
        # authoritative count from the provider before transitioning
        # status. Only write a non-zero count here; the FAILED branch
        # below leaves the existing counter alone. Pydantic aggregates
        # are frozen, so we use ``model_copy`` for them; for non-pydantic
        # callers (plain objects, test mocks) the attribute is set
        # directly without rebinding ``request``.
        if instance_count > 0:
            from pydantic import BaseModel as _PydanticBaseModel

            if isinstance(request, _PydanticBaseModel):
                request = request.model_copy(update={"successful_count": instance_count})
            else:
                try:
                    request.successful_count = instance_count  # type: ignore[attr-defined]
                except Exception as e:
                    # Best-effort: this branch runs on legacy non-pydantic
                    # request stand-ins in tests. Log so a real assignment
                    # failure on a live request doesn't disappear.
                    self._logger.warning(
                        "Failed to set successful_count on request %s: %s",
                        getattr(request, "request_id", "<unknown>"),
                        e,
                    )

        if instance_count == requested_count:
            # All requested instances fulfilled. Fleet API errors (e.g. AZ-
            # specific spot capacity warnings that were already routed around
            # by the fleet's instance-type ladder) are advisory in this
            # case — they did not prevent any capacity unit being met.
            # Marking the request PARTIAL would be misleading and locks it
            # in a terminal non-success state. The errors are still
            # persisted under request.metadata["fleet_errors"] and visible
            # in the drawer.
            if not fulfillment_final:
                # Provider returned all instance IDs synchronously but instances
                # are still 'pending' (booting). Keep request IN_PROGRESS so
                # check_hosts_status / ProviderFulfilment can promote it to
                # COMPLETED once running_count >= target.
                request = request.update_status(
                    RequestStatus.IN_PROGRESS,
                    f"{instance_count}/{requested_count} instances created — awaiting running state",
                )
            elif has_api_errors:
                request = request.update_status(
                    RequestStatus.COMPLETED,
                    f"All {instance_count} instances provisioned (with non-blocking provider warnings)",
                )
            else:
                request = request.update_status(
                    RequestStatus.COMPLETED,
                    "All instances provisioned successfully",
                )
        elif instance_count > 0:
            # When the provider has NOT signalled this is the final answer
            # (fulfillment_final=False — true for every async cloud provider
            # in the present codebase) we MUST NOT stamp PARTIAL here. PARTIAL
            # is terminal; once stamped, future sync cycles cannot reconcile
            # against the actual provider state. Stay IN_PROGRESS instead and
            # let the polling loop / ProviderFulfilment promote the request to
            # COMPLETED once running_count >= target.
            if not fulfillment_final:
                request = request.update_status(
                    RequestStatus.IN_PROGRESS,
                    f"{instance_count}/{requested_count} instances created — awaiting provider confirmation",
                )
            elif has_api_errors:
                request = request.update_status(
                    RequestStatus.PARTIAL,
                    f"Partial success: {instance_count}/{requested_count} instances created with API errors: {error_summary}",
                )
            else:
                request = request.update_status(
                    RequestStatus.PARTIAL,
                    f"Partially fulfilled: {instance_count}/{requested_count} instances",
                )
        elif request.resource_ids:
            request = request.update_status(
                RequestStatus.IN_PROGRESS,
                "Resources created, instances pending",
            )
        else:
            request = request.update_status(
                RequestStatus.FAILED,
                "No instances provisioned and no cloud resources created",
            )

        return request

    def _extract_machine_ids(self, result: ProvisioningResult) -> List[str]:
        """Extract machine IDs if available in provider result."""
        try:
            if result.machine_ids:
                return result.machine_ids
            elif result.instances:
                instances = result.instances
                if isinstance(instances, list) and instances:
                    return cast(
                        List[str],
                        [
                            str(instance["instance_id"])
                            for instance in instances
                            if instance.get("instance_id")
                        ],
                    )
            return []
        except (KeyError, TypeError, AttributeError) as e:
            self._logger.warning(
                "Failed to extract machine IDs from provider result: %s",
                e,
                exc_info=True,
            )
            return []

    def _create_machine_aggregate(
        self, instance_data: Dict[str, Any], request: Any, template_id: str
    ) -> Machine:
        """Create machine aggregate from instance data."""
        from datetime import datetime

        from orb.domain.base.value_objects import InstanceType
        from orb.domain.machine.machine_identifiers import MachineId
        from orb.domain.machine.machine_status import MachineStatus

        # Parse launch_time if it's a string
        launch_time = instance_data.get("launch_time")
        if isinstance(launch_time, str):
            try:
                launch_time = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
            except ValueError:
                launch_time = None

        return Machine(
            machine_id=MachineId(value=instance_data["instance_id"]),
            request_id=str(request.request_id),
            template_id=template_id,
            provider_type=request.provider_type,
            provider_name=request.provider_name,
            provider_api=request.provider_api,
            resource_id=instance_data.get("resource_id"),
            instance_type=InstanceType(value=instance_data.get("instance_type", "t2.micro")),
            image_id=instance_data.get("image_id", "unknown"),
            status=MachineStatus.PENDING,
            private_ip=instance_data.get("private_ip"),
            public_ip=instance_data.get("public_ip"),
            launch_time=launch_time,
            metadata=instance_data.get("metadata", {}),
        )
