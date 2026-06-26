"""Service for orchestrating provider provisioning operations."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, assert_never

if TYPE_CHECKING:
    from orb.domain.base.ports.provider_selection_port import ProviderSelectionPort
    from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy

from orb.domain.base.exceptions import QuotaError
from orb.domain.base.operation_outcome import (
    Accepted,
    Completed,
    Failed,
    OperationOutcome,
    RequiresFollowUp,
)
from orb.domain.base.ports import ConfigurationPort, ContainerPort, LoggingPort, ProviderConfigPort
from orb.domain.base.results import ProviderSelectionResult
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.resilience.exceptions import CircuitBreakerOpenError


@dataclass
class ProvisioningResult:
    """Result of provisioning operation.

    The ``outcome`` field carries the typed :class:`OperationOutcome` returned
    by the provider.  ``is_final`` is preserved for backward compatibility and
    is derived from ``outcome`` when it is present.

    ``Accepted``         → ``is_final = False`` (provider is still processing)
    ``Completed``        → ``is_final = True``
    ``RequiresFollowUp`` → ``is_final = False`` (background work remains)
    ``Failed``           → ``is_final = True``
    ``None`` (legacy)    → honour the explicit ``is_final`` value

    Provider error fields (all optional, only set on failure):
      ``provider_error_code``    — provider API error code (e.g. ``UnauthorizedOperation``)
      ``provider_error_message`` — human-readable message from the provider response
      ``provider_request_id``    — provider request ID for support cases
      ``error_source``           — service.operation label (e.g. ``aws.ec2.RunInstances``)
    """

    success: bool
    resource_ids: list[str]
    machine_ids: list[str]
    instances: list[dict[str, Any]]
    provider_data: dict[str, Any]
    error_message: str | None = None
    fulfilled_count: int = 0
    is_final: bool = True
    outcome: OperationOutcome | None = field(default=None)
    # Provider error detail fields — populated when the failure originates from
    # a provider API call so callers can surface actionable diagnostics to the user.
    provider_error_code: str | None = None
    provider_error_message: str | None = None
    provider_request_id: str | None = None
    error_source: str | None = None

    def __post_init__(self) -> None:
        """Derive ``is_final`` from ``outcome`` when an outcome is provided."""
        if self.outcome is not None:
            match self.outcome:
                case Accepted():
                    self.is_final = False
                case Completed():
                    self.is_final = True
                case RequiresFollowUp():
                    self.is_final = False
                case Failed():
                    self.is_final = True
                case _ as unreachable:
                    assert_never(unreachable)


def _extract_aws_error_fields(exc: BaseException) -> dict[str, Any]:
    """Extract provider error fields from a provider exception (if applicable).

    Returns a dict suitable for **-unpacking into ProvisioningResult.  When the
    exception carries no provider error attributes the dict will contain only
    None values so the ProvisioningResult fields stay empty (safe default).
    """
    provider_error_code: str | None = getattr(exc, "aws_error_code", None)
    provider_error_message: str | None = getattr(exc, "aws_error_message", None)
    provider_request_id: str | None = getattr(exc, "aws_request_id", None)
    error_source: str | None = getattr(exc, "error_source", None)
    return {
        "provider_error_code": provider_error_code,
        "provider_error_message": provider_error_message,
        "provider_request_id": provider_request_id,
        "error_source": error_source,
    }


class ProvisioningOrchestrationService:
    """Service for orchestrating provider provisioning operations."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        provider_selection_port: "ProviderSelectionPort",
        provider_config_port: ProviderConfigPort,
        config_port: ConfigurationPort,
        circuit_breaker_factory: Callable[[str], "CircuitBreakerStrategy"],
    ):
        self._container = container
        self._logger = logger
        self._provider_selection_port = provider_selection_port
        self._provider_config_port = provider_config_port
        self._config_port = config_port
        self._circuit_breaker_factory = circuit_breaker_factory

    async def execute_provisioning(
        self, template: Template, request: Request, selection_result: ProviderSelectionResult
    ) -> ProvisioningResult:
        """Execute provisioning with capacity top-up retry loop."""
        request_config = self._config_port.get_request_config()
        default_config: dict[str, Any] = {
            "max_retries": request_config.get("fulfillment_max_retries", 3),
            "timeout_seconds": request_config.get("fulfillment_timeout_seconds", 300),
            "dispatch_timeout_seconds": request_config.get("dispatch_timeout_seconds", 300),
            "batch_size": request_config.get("fulfillment_batch_size", 1000),
            "fallback_template_id": request_config.get("fulfillment_fallback_template_id"),
        }
        config = {**default_config, **request.metadata.get("fulfillment_config", {})}
        max_retries: int = int(config["max_retries"])
        timeout_seconds: float = float(config["timeout_seconds"])
        dispatch_timeout_seconds: float = float(config["dispatch_timeout_seconds"])
        batch_size: int = int(config["batch_size"])

        started_at = datetime.now(timezone.utc)
        remaining = request.requested_count
        attempt_number = 0
        consecutive_zero_fulfillments = 0

        accumulated_resource_ids: list[str] = []
        accumulated_machine_ids: list[str] = []
        accumulated_instances: list[dict[str, Any]] = []
        accumulated_provider_data: dict[str, Any] = {}
        last_result: ProvisioningResult | None = None

        while remaining > 0 and attempt_number <= max_retries:
            elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
            if elapsed >= timeout_seconds:
                self._logger.warning(
                    "Provisioning timeout after %.1fs for request %s",
                    elapsed,
                    request.request_id,
                )
                break

            attempt_number += 1
            attempt_count = min(remaining, batch_size)
            attempt_started = datetime.now(timezone.utc)

            self._logger.info(
                "Provisioning attempt %d/%d: requesting %d of %d remaining instances",
                attempt_number,
                max_retries + 1,
                attempt_count,
                remaining,
            )

            # Stamp attempt number so provider-level idempotency tokens are unique per attempt
            request = request.update_metadata({"provisioning_attempt": attempt_number})

            try:
                last_result = await self._dispatch_single_attempt(
                    template, request, selection_result, attempt_count, dispatch_timeout_seconds
                )
            except Exception as e:
                if not isinstance(e, CircuitBreakerOpenError):
                    raise
                self._logger.warning(
                    "Circuit breaker open for provider %s — aborting retry loop: %s",
                    selection_result.provider_name,
                    e,
                )
                self._record_provider_failure(selection_result.provider_name)
                break

            attempt_completed = datetime.now(timezone.utc)

            # Accumulate results
            accumulated_resource_ids.extend(last_result.resource_ids)
            accumulated_machine_ids.extend(last_result.machine_ids)
            accumulated_instances.extend(last_result.instances)
            accumulated_provider_data.update(last_result.provider_data)

            fulfilled_this_attempt = last_result.fulfilled_count
            remaining -= fulfilled_this_attempt

            if fulfilled_this_attempt == 0 and last_result.success:
                consecutive_zero_fulfillments += 1
                if consecutive_zero_fulfillments >= 3:
                    self._logger.warning(
                        "Breaking retry loop after %d consecutive zero-fulfillment attempts",
                        consecutive_zero_fulfillments,
                    )
                    break
            else:
                consecutive_zero_fulfillments = 0

            # Append to fulfillment_attempts audit trail
            attempt_record = {
                "attempt": attempt_number,
                "requested": attempt_count,
                "fulfilled": fulfilled_this_attempt,
                "resource_ids": last_result.resource_ids,
                "started_at": attempt_started.isoformat(),
                "completed_at": attempt_completed.isoformat(),
            }
            existing_attempts = list(request.metadata.get("fulfillment_attempts", []))
            existing_attempts.append(attempt_record)
            request = request.update_metadata({"fulfillment_attempts": existing_attempts})

            if not last_result.success:
                self._logger.warning(
                    "Attempt %d failed: %s", attempt_number, last_result.error_message
                )
                self._record_provider_failure(selection_result.provider_name)
                break

            if remaining > 0 and not last_result.is_final:
                # Partial fulfillment, retry may help — persist ACQUIRING status
                self._logger.info(
                    "Attempt %d: %d/%d fulfilled, %d remaining — retrying",
                    attempt_number,
                    request.requested_count - remaining,
                    request.requested_count,
                    remaining,
                )
                request, persist_ok = await asyncio.to_thread(self._persist_acquiring, request)
                if not persist_ok:
                    self._logger.warning(
                        "ACQUIRING persist failed for request %s on attempt %d — "
                        "continuing retry loop with in-memory state",
                        request.request_id,
                        attempt_number,
                    )
            elif last_result.is_final:
                # No point retrying
                break

        total_fulfilled = len(accumulated_instances)
        success = last_result.success if last_result else False

        return ProvisioningResult(
            success=success,
            resource_ids=accumulated_resource_ids,
            machine_ids=accumulated_machine_ids,
            instances=accumulated_instances,
            provider_data=accumulated_provider_data,
            error_message=last_result.error_message if last_result else "No provisioning attempted",
            fulfilled_count=total_fulfilled,
            is_final=last_result.is_final if last_result else True,
        )

    def _persist_acquiring(self, request: Request) -> tuple[Request, bool]:
        """Persist request with ACQUIRING status between retry attempts.

        Returns:
            (updated_request, success) — success is False when the DB write
            failed.  The caller should log a warning but continue the retry loop
            because the in-memory request is still valid.
        """
        from orb.domain.base import UnitOfWorkFactory

        try:
            updated = request.update_status(
                RequestStatus.ACQUIRING, "Partial fulfillment, retrying"
            )
            uow_factory = self._container.get(UnitOfWorkFactory)
            with uow_factory.create_unit_of_work() as uow:
                uow.requests.save(updated)
            return updated, True
        except Exception as e:
            self._logger.warning("Failed to persist ACQUIRING status: %s", e)
            return request, False

    def _record_provider_success(self, provider_name: str) -> None:
        """Reset circuit breaker failure count after a successful dispatch."""
        cb_key = f"provider:{provider_name}"
        try:
            cb = self._circuit_breaker_factory(cb_key)
            if cb.has_state(cb_key):
                cb.record_success()
        except Exception as e:
            self._logger.warning(
                "Failed to reset circuit breaker state for %s: %s", provider_name, e
            )

    def _record_provider_failure(self, provider_name: str) -> None:
        """Increment circuit breaker failure count and open circuit if threshold is reached."""
        import time

        cb_key = f"provider:{provider_name}"
        try:
            cb = self._circuit_breaker_factory(cb_key)
            if not cb.has_state(cb_key):
                return
            cb.record_failure(time.time())
        except Exception as e:
            self._logger.warning(
                "Failed to record circuit breaker failure for %s: %s", provider_name, e
            )

    async def _dispatch_single_attempt(
        self,
        template: Template,
        request: Request,
        selection_result: ProviderSelectionResult,
        count: int,
        dispatch_timeout_seconds: float = 300.0,
    ) -> ProvisioningResult:
        """Dispatch a single provisioning attempt for `count` instances."""
        try:
            from orb.application.ports.scheduler_port import SchedulerPort
            from orb.domain.base.operations import (
                Operation as ProviderOperation,
                OperationType as ProviderOperationType,
            )

            scheduler = self._container.get(SchedulerPort)

            operation = ProviderOperation(
                operation_type=ProviderOperationType.CREATE_INSTANCES,
                parameters={
                    "template_config": scheduler.format_template_for_provider(template),
                    "count": count,
                    "request_id": str(request.request_id),
                    "request_metadata": dict(request.metadata),
                },
                context={
                    "correlation_id": str(request.request_id),
                    "request_id": str(request.request_id),
                    "dry_run": request.metadata.get("dry_run", False),
                },
            )

            self._provider_config_port.get_provider_instance_config(selection_result.provider_name)

            async with asyncio.timeout(dispatch_timeout_seconds):
                result = await self._provider_selection_port.execute_operation(
                    selection_result.provider_name, operation
                )

            if result.success:
                self._logger.debug("Provider result.data: %s", result.data)
                self._logger.debug("Provider result.metadata: %s", result.metadata)

                resource_ids = result.data.get("resource_ids", [])
                instances = result.data.get("instances", [])

                # Handler provider_data is now flat-merged into result.metadata
                # by the instance-operation service. Read fulfillment signals
                # from the top-level metadata. Falls back to result.data's
                # provider_data sub-key for handlers that have not yet been
                # migrated to the flat shape (e.g. legacy code paths).
                metadata_dict: dict[str, Any] = dict(result.metadata or {})
                legacy_pd = result.data.get("provider_data") or {}
                if isinstance(legacy_pd, dict):
                    for k, v in legacy_pd.items():
                        metadata_dict.setdefault(k, v)

                fulfillment_final = bool(metadata_dict.get("fulfillment_final", False))
                has_capacity_error = bool(  # noqa: F841 — retained for telemetry consumers
                    metadata_dict.get("capacity_constrained", False)
                )

                merged_provider_data: dict[str, Any] = dict(metadata_dict)
                if result.routing_info:
                    merged_provider_data.update(result.routing_info)

                # fulfillment_final=True means the provider has finished
                # provisioning and the result is the final word — emit
                # Completed. False means instances exist but may still be
                # pending; emit Accepted so the polling loop owns the final
                # status transition.
                if fulfillment_final:
                    outcome: OperationOutcome = Completed(
                        resource_ids=resource_ids,
                        metadata=merged_provider_data,
                    )
                else:
                    outcome = Accepted(
                        request_id=str(request.request_id),
                        pending_resource_ids=resource_ids,
                        metadata=merged_provider_data,
                    )

                self._record_provider_success(selection_result.provider_name)
                return ProvisioningResult(
                    success=True,
                    resource_ids=resource_ids,
                    machine_ids=result.data.get("instance_ids", []),
                    instances=instances,
                    provider_data=merged_provider_data,
                    fulfilled_count=len(instances),
                    outcome=outcome,
                    # is_final derived from outcome in __post_init__
                )
            else:
                return ProvisioningResult(
                    success=False,
                    resource_ids=[],
                    machine_ids=[],
                    instances=[],
                    provider_data=result.metadata or {},
                    error_message=result.error_message,
                    outcome=Failed(
                        error=result.error_message or "Provider returned failure",
                        recoverable=False,
                    ),
                )

        except CircuitBreakerOpenError:
            raise  # do not swallow — let it propagate to execute_provisioning

        except TimeoutError:
            timeout_msg = f"Dispatch timed out after {dispatch_timeout_seconds:.0f}s"
            self._logger.warning(
                "Dispatch timed out after %.1fs for provider %s (request %s)",
                dispatch_timeout_seconds,
                selection_result.provider_name,
                str(request.request_id) if hasattr(request, "request_id") else "unknown",
            )
            return ProvisioningResult(
                success=False,
                resource_ids=[],
                machine_ids=[],
                instances=[],
                provider_data={},
                error_message=timeout_msg,
                outcome=Failed(error=timeout_msg, recoverable=True),
            )

        except QuotaError as e:
            quota_msg = f"Quota exceeded: {e}"
            self._logger.error(
                "Quota error during provisioning for template %s: %s",
                template.template_id if hasattr(template, "template_id") else "unknown",
                e,
                extra={
                    "request_id": str(request.request_id)
                    if hasattr(request, "request_id")
                    else None,
                    "provider_name": selection_result.provider_name if selection_result else None,
                    "error_type": type(e).__name__,
                },
            )
            aws_fields = _extract_aws_error_fields(e)
            return ProvisioningResult(
                success=False,
                resource_ids=[],
                machine_ids=[],
                instances=[],
                provider_data={},
                error_message=quota_msg,
                outcome=Failed(error=quota_msg, recoverable=False),
                **aws_fields,
            )

        except Exception as e:
            generic_msg = f"Provisioning failed: {e}"
            self._logger.error(
                "Provisioning dispatch failed for template %s: %s",
                template.template_id if hasattr(template, "template_id") else "unknown",
                e,
                exc_info=True,
                extra={
                    "request_id": str(request.request_id)
                    if hasattr(request, "request_id")
                    else None,
                    "provider_name": selection_result.provider_name if selection_result else None,
                    "error_type": type(e).__name__,
                },
            )
            aws_fields = _extract_aws_error_fields(e)
            return ProvisioningResult(
                success=False,
                resource_ids=[],
                machine_ids=[],
                instances=[],
                provider_data={},
                error_message=generic_msg,
                outcome=Failed(error=generic_msg, recoverable=False),
                **aws_fields,
            )
