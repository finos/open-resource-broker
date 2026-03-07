"""Service for orchestrating provider provisioning operations."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orb.domain.base.ports.provider_selection_port import ProviderSelectionPort

from orb.domain.base.exceptions import QuotaError
from orb.domain.base.ports import ConfigurationPort, ContainerPort, LoggingPort, ProviderConfigPort
from orb.domain.base.results import ProviderSelectionResult
from orb.domain.request.aggregate import Request
from orb.domain.request.request_types import RequestStatus
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.resilience.exceptions import CircuitBreakerOpenError
from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitBreakerStrategy


@dataclass
class ProvisioningResult:
    """Result of provisioning operation."""

    success: bool
    resource_ids: list[str]
    machine_ids: list[str]
    instances: list[dict[str, Any]]
    provider_data: dict[str, Any]
    error_message: str | None = None
    fulfilled_count: int = 0
    is_final: bool = True


class ProvisioningOrchestrationService:
    """Service for orchestrating provider provisioning operations."""

    def __init__(
        self,
        container: ContainerPort,
        logger: LoggingPort,
        provider_selection_port: "ProviderSelectionPort",
        provider_config_port: ProviderConfigPort,
        config_port: ConfigurationPort | None = None,
    ):
        self._container = container
        self._logger = logger
        self._provider_selection_port = provider_selection_port
        self._provider_config_port = provider_config_port
        self._config_port = config_port

    async def execute_provisioning(
        self, template: Template, request: Request, selection_result: ProviderSelectionResult
    ) -> ProvisioningResult:
        """Execute provisioning with capacity top-up retry loop."""
        if self._config_port is not None:
            request_config = self._config_port.get_request_config()
            default_config: dict[str, Any] = {
                "max_retries": request_config.get("fulfillment_max_retries", 3),
                "timeout_seconds": request_config.get("fulfillment_timeout_seconds", 300),
                "batch_size": request_config.get("fulfillment_batch_size", 1000),
                "fallback_template_id": request_config.get("fulfillment_fallback_template_id"),
            }
        else:
            default_config = {
                "max_retries": 3,
                "timeout_seconds": 300,
                "batch_size": 1000,
                "fallback_template_id": None,
            }
        config = {**default_config, **request.metadata.get("fulfillment_config", {})}
        max_retries: int = int(config["max_retries"])
        timeout_seconds: float = float(config["timeout_seconds"])
        batch_size: int = int(config["batch_size"])

        started_at = datetime.now(timezone.utc)
        remaining = request.requested_count
        attempt_number = 0

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
            request.metadata["provisioning_attempt"] = attempt_number

            try:
                last_result = await self._dispatch_single_attempt(
                    template, request, selection_result, attempt_count
                )
            except CircuitBreakerOpenError as e:
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

            # Append to fulfillment_attempts audit trail
            attempt_record = {
                "attempt": attempt_number,
                "requested": attempt_count,
                "fulfilled": fulfilled_this_attempt,
                "resource_ids": last_result.resource_ids,
                "started_at": attempt_started.isoformat(),
                "completed_at": attempt_completed.isoformat(),
            }
            if "fulfillment_attempts" not in request.metadata:
                request.metadata["fulfillment_attempts"] = []
            request.metadata["fulfillment_attempts"].append(attempt_record)

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
                request = self._persist_acquiring(request)
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

    def _persist_acquiring(self, request: Request) -> Request:
        """Persist request with ACQUIRING status between retry attempts."""
        from orb.domain.base import UnitOfWorkFactory

        try:
            updated = request.update_status(
                RequestStatus.ACQUIRING, "Partial fulfillment, retrying"
            )
            uow_factory = self._container.get(UnitOfWorkFactory)
            with uow_factory.create_unit_of_work() as uow:
                uow.requests.save(updated)
            return updated
        except Exception as e:
            self._logger.warning("Failed to persist ACQUIRING status: %s", e)
            return request

    def _record_provider_success(self, provider_name: str) -> None:
        """Reset circuit breaker failure count after a successful dispatch."""
        try:
            state = CircuitBreakerStrategy._circuit_states.get(provider_name)
            if state is not None:
                state["failure_count"] = 0
        except Exception as e:
            self._logger.warning(
                "Failed to reset circuit breaker state for %s: %s", provider_name, e
            )

    def _record_provider_failure(self, provider_name: str) -> None:
        """Increment circuit breaker failure count and open circuit if threshold is reached."""
        import time

        from orb.infrastructure.resilience.strategy.circuit_breaker import CircuitState

        cb_key = f"provider:{provider_name}"
        try:
            state = CircuitBreakerStrategy._circuit_states.get(cb_key)
            if state is None:
                return
            state["failure_count"] += 1
            state["last_failure_time"] = time.time()
            # Use the default failure_threshold (5) — same default as CircuitBreakerStrategy
            failure_threshold = 5
            if (
                state["state"] == CircuitState.CLOSED
                and state["failure_count"] >= failure_threshold
            ):
                state["state"] = CircuitState.OPEN
                self._logger.warning(
                    "Circuit breaker opened for provider %s after %d failures",
                    provider_name,
                    state["failure_count"],
                )
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
    ) -> ProvisioningResult:
        """Dispatch a single provisioning attempt for `count` instances."""
        try:
            from orb.domain.base.operations import (
                Operation as ProviderOperation,
                OperationType as ProviderOperationType,
            )
            from orb.domain.base.ports.scheduler_port import SchedulerPort

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

            result = await self._provider_selection_port.execute_operation(
                selection_result.provider_name, operation
            )

            if result.success:
                self._logger.info("Provider result.data: %s", result.data)
                self._logger.info("Provider result.metadata: %s", result.metadata)

                resource_ids = result.data.get("resource_ids", [])
                instances = result.data.get("instances", [])

                provider_data = result.data.get("provider_data", None) or (
                    result.metadata or {}
                ).get("provider_data", {})
                fleet_errors = provider_data.get("fleet_errors") or []
                fulfillment_final = provider_data.get("fulfillment_final", False)
                capacity_error_codes = {
                    "InsufficientInstanceCapacity",
                    "SpotMaxPriceTooLow",
                    "MaxSpotInstanceCountExceeded",
                }
                has_capacity_error = any(
                    e.get("error_code") in capacity_error_codes for e in fleet_errors
                )

                self._record_provider_success(selection_result.provider_name)
                return ProvisioningResult(
                    success=True,
                    resource_ids=resource_ids,
                    machine_ids=result.data.get("instance_ids", []),
                    instances=instances,
                    provider_data=result.metadata or {},
                    fulfilled_count=len(instances),
                    is_final=(not has_capacity_error and len(instances) >= count)
                    or fulfillment_final,
                )
            else:
                return ProvisioningResult(
                    success=False,
                    resource_ids=[],
                    machine_ids=[],
                    instances=[],
                    provider_data=result.metadata or {},
                    error_message=result.error_message,
                )

        except CircuitBreakerOpenError:
            raise  # do not swallow — let it propagate to execute_provisioning

        except QuotaError as e:
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
            return ProvisioningResult(
                success=False,
                resource_ids=[],
                machine_ids=[],
                instances=[],
                provider_data={},
                error_message=f"Quota exceeded: {e}",
                is_final=True,
            )

        except Exception as e:
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
            return ProvisioningResult(
                success=False,
                resource_ids=[],
                machine_ids=[],
                instances=[],
                provider_data={},
                error_message=f"Provisioning failed: {e}",
            )
