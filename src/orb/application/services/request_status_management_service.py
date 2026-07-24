"""Service for managing request status updates and persistence."""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, cast

from orb.domain.base import UnitOfWorkFactory
from orb.domain.base.ports import LoggingPort

if TYPE_CHECKING:
    from orb.domain.base.diagnostic import FulfilmentDiagnostic
from orb.domain.base.provider_fulfilment import ProviderFulfilment
from orb.domain.machine.aggregate import Machine
from orb.domain.request.aggregate import Request
from orb.domain.request.fulfilment_state_machine import (
    FulfilmentEvent,
    FulfilmentStateMachine,
)

from .provisioning_orchestration_service import ProvisioningResult

# Default grace period used only when no FulfilmentStateMachine is injected
# (lightweight test construction). Production injects the config-driven machine.
_DEFAULT_GRACE_PERIOD_SECONDS = 3600


class RequestStatusManagementService:
    """Service for managing request status updates and persistence."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        state_machine: Optional[FulfilmentStateMachine] = None,
    ):
        self._uow_factory = uow_factory
        self._logger = logger
        self._state_machine = state_machine or FulfilmentStateMachine(
            grace_period_seconds=_DEFAULT_GRACE_PERIOD_SECONDS
        )

    def _apply_verdict(
        self,
        request: Any,
        state: str,
        message: str,
        *,
        event: FulfilmentEvent = FulfilmentEvent.PROVIDER_VERDICT,
        fulfilment: Optional[ProviderFulfilment] = None,
        final: bool = False,
        diagnostic: Any = None,
    ) -> Any:
        """Route a status transition through the fulfilment state machine.

        For real ``Request`` aggregates the transition is driven by the state
        machine (single write authority). For non-pydantic stand-ins used in
        tests, fall back to the aggregate-style ``update_status`` so those
        callers keep working.

        ``final`` carries the caller's finality signal onto the synthesised
        verdict when no explicit ``fulfilment`` is supplied. A synchronous
        provider whose launch API has already settled (e.g. AWS RunInstances)
        reports a settled shortfall: marking the synthesised ``partial`` verdict
        ``final=True`` lets the state machine terminalise it immediately rather
        than parking it in the PARTIAL_PENDING holding state to await capacity
        that can never arrive. Asynchronous providers leave it ``False`` so a
        transient partial can still heal.
        """
        from orb.domain.request.exceptions import InvalidRequestStateError
        from orb.domain.request.request_types import RequestStatus

        now = datetime.now(timezone.utc)
        if isinstance(request, Request):
            try:
                if event == FulfilmentEvent.PROVIDER_VERDICT:
                    verdict = fulfilment or ProviderFulfilment(
                        state=state,  # type: ignore[arg-type]
                        message=message,
                        final=final,
                    )
                    return self._state_machine.apply(
                        request,
                        FulfilmentEvent.PROVIDER_VERDICT,
                        now=now,
                        fulfilment=verdict,
                        message=message,
                        diagnostic=diagnostic,
                    )
                return self._state_machine.apply(
                    request,
                    event,
                    now=now,
                    message=message,
                    diagnostic=diagnostic,
                )
            except InvalidRequestStateError:
                self._logger.debug(
                    "State machine rejected transition to %s for request %s",
                    state,
                    getattr(request, "request_id", "<unknown>"),
                )
                return request

        # Non-pydantic fallback (test mocks) — map verdict state to a status.
        fallback_status = {
            "fulfilled": RequestStatus.COMPLETED,
            "in_progress": RequestStatus.IN_PROGRESS,
            "partial": RequestStatus.PARTIAL,
            "failed": RequestStatus.FAILED,
        }.get(state, RequestStatus.IN_PROGRESS)
        return request.update_status(fallback_status, message)

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

        # Surface the provider's *classified* diagnostic (auth / validation /
        # throttle / capacity) onto the aggregate. The handler stores it under
        # provider_data["fulfilment_diagnostic"] as a JSON-mode dict; without
        # merging it here the headline "WHY did it fall short" signal never
        # reaches request.fulfilment_diagnostic and only a generic capacity
        # shortfall would be shown.
        request = self._merge_provider_diagnostic(request, provider_data)

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
        # Azure/GCP/K8s). Per-handler requires_async_polling flags inside
        # provider_data are read at the orchestration layer to set is_final;
        # this service trusts the derived OperationOutcome exclusively.
        return self._update_request_status(
            request,
            len(instances),
            request.requested_count,
            has_api_errors,
            provider_errors,
            fulfillment_final=provisioning_result.is_final,
        )

    def _merge_provider_diagnostic(self, request: Any, provider_data: Any) -> Any:
        """Merge a provider-classified diagnostic onto the request aggregate.

        The AWS handlers classify their fleet/spot/ASG errors into a
        ``FulfilmentDiagnostic`` and stash it (JSON-mode dict) under
        ``provider_data["fulfilment_diagnostic"]``. This lifts that classified
        category (auth / validation / throttle / capacity) onto
        ``request.fulfilment_diagnostic`` via the aggregate's merge-on-write
        ``set_fulfilment_diagnostic`` so the *why* survives to the DTO.

        Best-effort: a malformed / missing diagnostic is skipped without
        disturbing the status flow. Only applies to real ``Request`` aggregates.
        """
        if not isinstance(request, Request):
            return request
        if not isinstance(provider_data, dict):
            return request
        raw = provider_data.get("fulfilment_diagnostic")
        if not raw:
            return request
        try:
            from orb.domain.base.diagnostic import FulfilmentDiagnostic

            if isinstance(raw, FulfilmentDiagnostic):
                diagnostic = raw
            elif isinstance(raw, dict):
                diagnostic = FulfilmentDiagnostic.model_validate(raw)
            else:
                return request
            return request.set_fulfilment_diagnostic(diagnostic)
        except Exception as e:
            self._logger.warning(
                "Failed to merge provider fulfilment diagnostic for request %s: %s",
                getattr(request, "request_id", "<unknown>"),
                e,
            )
            return request

    def _handle_provisioning_failure(self, request: Any, provisioning_result: Any) -> Any:
        """Handle provisioning failure, capturing provider error details when available.

        The provider error is run through the shared classifier so a hard
        failure (``InsufficientInstanceCapacity`` -> CAPACITY,
        ``UnauthorizedOperation`` -> AUTH, ...) is categorised correctly instead
        of being flattened to INTERNAL. The user-facing ``summary`` is the safe
        category template; the raw provider message is kept only in ``detail``
        (and in ``error_details``/metadata below), never in the summary.
        """
        error_message = (
            provisioning_result.error_message or "Provisioning failed (no error details)"
        )
        failure_diagnostic = self._classify_failure_diagnostic(provisioning_result, error_message)
        request = self._apply_verdict(
            request,
            "failed",
            f"Provisioning failed: {error_message}",
            event=FulfilmentEvent.FAIL,
            diagnostic=failure_diagnostic,
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
                # Preserve the FAILED status-change event emitted by the FAIL
                # verdict above — a plain model_validate would drop _domain_events
                # and subscribers would never observe the failure.
                request = request._rebuild_preserving_events(fields)
            else:
                # Fallback for mock objects in tests
                request.error_details = current  # type: ignore[attr-defined]

        return request

    def _classify_failure_diagnostic(
        self, provisioning_result: Any, error_message: str
    ) -> "FulfilmentDiagnostic":
        """Classify a provisioning failure into a categorised diagnostic.

        Assembles the provider error signals available on the failed result
        (``fleet_errors`` in ``provider_data``, plus the top-level
        ``provider_error_code`` / ``error_message``) and runs them through the
        shared classifier so a hard failure gets the right category
        (CAPACITY / AUTH / VALIDATION / RATE_LIMIT) rather than a blanket
        INTERNAL. The summary is the safe category template; the raw provider
        message is preserved only in ``detail``.
        """
        from orb.domain.base.diagnostic import DiagnosticCategory, FulfilmentDiagnostic
        from orb.domain.base.error_classification import classify_provider_errors

        now = datetime.now(timezone.utc)
        provider_error_code = getattr(provisioning_result, "provider_error_code", None)
        provider_error_message = getattr(provisioning_result, "provider_error_message", None)
        detail = provider_error_code if isinstance(provider_error_code, str) else None

        # Collect classifiable error records. Prefer the structured fleet_errors
        # the provider already normalised; otherwise synthesise a single record
        # from the top-level provider_error_code so a hard API failure that did
        # not populate fleet_errors is still categorised.
        errors: list[dict[str, Any]] = []
        provider_data = getattr(provisioning_result, "provider_data", None)
        if isinstance(provider_data, dict):
            fleet_errors = provider_data.get("fleet_errors")
            if isinstance(fleet_errors, list):
                errors.extend(e for e in fleet_errors if isinstance(e, dict))
        if not errors and isinstance(provider_error_code, str) and provider_error_code:
            errors.append(
                {
                    "error_code": provider_error_code,
                    "error_message": (
                        provider_error_message
                        if isinstance(provider_error_message, str)
                        else error_message
                    ),
                }
            )

        if not errors:
            # No provider error signal at all (e.g. dispatch timeout, generic
            # exception) — keep the INTERNAL categorisation, raw detail in detail.
            return FulfilmentDiagnostic(
                category=DiagnosticCategory.INTERNAL,
                summary="Provisioning failed",
                detail=detail or error_message,
                occurred_at=now,
            )

        classified = classify_provider_errors(errors, now=now)
        # Keep the safe category-templated summary; attach the raw provider code
        # as detail so operators still have the actionable specifics.
        return classified.model_copy(update={"detail": detail or classified.detail})

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
            # Marking the request partial would be misleading. The errors are
            # still persisted under request.metadata["fleet_errors"].
            if not fulfillment_final:
                # Provider returned all instance IDs synchronously but instances
                # are still 'pending' (booting). Keep request in progress so a
                # later provider verdict can promote it to COMPLETED once
                # running_count >= target.
                request = self._apply_verdict(
                    request,
                    "in_progress",
                    f"{instance_count}/{requested_count} instances created — awaiting running state",
                )
            elif has_api_errors:
                request = self._apply_verdict(
                    request,
                    "fulfilled",
                    f"All {instance_count} instances provisioned (with non-blocking provider warnings)",
                )
            else:
                request = self._apply_verdict(
                    request,
                    "fulfilled",
                    "All instances provisioned successfully",
                )
        elif instance_count > 0:
            # When the provider has NOT signalled this is the final answer
            # (fulfillment_final=False) stay in progress. When it IS final and
            # the count fell short, emit a partial verdict: the state machine
            # resolves it to the non-terminal PARTIAL_PENDING holding state
            # while within the deadline (so a later sync can still complete it)
            # or to terminal PARTIAL once the deadline passes.
            if not fulfillment_final:
                request = self._apply_verdict(
                    request,
                    "in_progress",
                    f"{instance_count}/{requested_count} instances created — awaiting provider confirmation",
                )
            elif has_api_errors:
                request = self._apply_verdict(
                    request,
                    "partial",
                    f"Partial success: {instance_count}/{requested_count} instances created with API errors: {error_summary}",
                    final=fulfillment_final,
                )
            else:
                request = self._apply_verdict(
                    request,
                    "partial",
                    f"Partially fulfilled: {instance_count}/{requested_count} instances",
                    final=fulfillment_final,
                )
        elif request.resource_ids:
            request = self._apply_verdict(
                request,
                "in_progress",
                "Resources created, instances pending",
            )
        else:
            request = self._apply_verdict(
                request,
                "failed",
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
