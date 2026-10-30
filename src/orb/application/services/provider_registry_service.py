"""Application service interface for provider registry access."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort
from orb.domain.base.results import ProviderSelectionResult, ValidationResult
from orb.domain.services.template_validation_domain_service import TemplateValidationDomainService
from orb.domain.template.template_aggregate import Template

HEALTH_PROBE_TIMEOUT_SECONDS = 5.0
HEALTH_PROBE_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    """Outcome of a provider health probe.

    ``status`` is ``healthy``, ``unhealthy`` (probe failed, timed out, or the
    provider reported itself unhealthy) or ``unknown`` (no strategy to probe).
    ``message`` is deliberately generic: provider health text can embed account
    identifiers or raw exception text, which is logged server-side instead.
    ``sample_count`` is the number of live probes run for this provider since
    process start.
    """

    provider_name: str
    status: str
    message: str
    response_time_ms: Optional[float] = None
    sample_count: int = 0
    checked_at: float = 0.0


class ProviderRegistryService:
    """Application service interface for provider registry operations."""

    def __init__(
        self,
        registry: ProviderRegistryPort,
        validation_service: TemplateValidationDomainService,
        logger: LoggingPort,
    ):
        self._registry = registry
        self._validation_service = validation_service
        self._logger = logger
        self._health_cache: dict[str, ProviderHealthSnapshot] = {}
        self._health_inflight: dict[str, asyncio.Task[ProviderHealthSnapshot]] = {}
        self._health_samples: dict[str, int] = {}

    def select_provider_for_template(
        self, template: Template, provider_name: str | None = None
    ) -> ProviderSelectionResult:
        return self._registry.select_provider_for_template(template, provider_name, self._logger)

    def select_active_provider(
        self,
        *,
        provider_name: str | None = None,
        provider_type: str | None = None,
    ) -> ProviderSelectionResult:
        return self._registry.select_active_provider(
            self._logger, provider_name=provider_name, provider_type=provider_type
        )

    def validate_template_requirements(
        self, template: Template, provider_instance: str
    ) -> ValidationResult:
        return self._validation_service.validate_template_requirements(template, provider_instance)

    async def execute_operation(self, provider_id: str, operation: Any) -> Any:
        strategy = self._registry.get_or_create_strategy(provider_id)
        if strategy is None:
            raise ValueError(f"No strategy found for provider: {provider_id}")
        return await strategy.execute_operation(operation)

    def get_strategy_capabilities(self, provider_id: str) -> Any:
        strategy = self._registry.get_or_create_strategy(provider_id)
        if strategy is None:
            return None
        return strategy.get_capabilities()

    def get_available_strategies(self) -> list[str]:
        return (
            self._registry.get_registered_providers()
            + self._registry.get_registered_provider_instances()
        )

    def register_provider_strategy(self, provider_type: str, config: Any = None) -> bool:
        return self._registry.ensure_provider_type_registered(provider_type)

    def check_strategy_health(self, provider_id: str) -> Any:
        strategy = self._registry.get_or_create_strategy(provider_id)
        if strategy is None:
            return None
        return strategy.check_health()

    async def probe_strategy_health(
        self,
        provider_id: str,
        *,
        timeout: float = HEALTH_PROBE_TIMEOUT_SECONDS,
        ttl: float = HEALTH_PROBE_TTL_SECONDS,
    ) -> ProviderHealthSnapshot:
        """Probe a provider's health without blocking the event loop.

        The strategy's ``check_health`` is a blocking network call, so it runs
        in a worker thread under ``timeout``. A result younger than ``ttl``
        seconds is reused and concurrent callers share one in-flight probe, so
        frequent polling cannot hammer the provider API. Never raises.
        """
        cached = self._health_cache.get(provider_id)
        now = time.monotonic()
        if cached is not None and now - cached.checked_at < ttl:
            return cached

        inflight = self._health_inflight.get(provider_id)
        if inflight is None or inflight.get_loop() is not asyncio.get_running_loop():
            inflight = asyncio.get_running_loop().create_task(
                self._run_health_probe(provider_id, timeout)
            )
            self._health_inflight[provider_id] = inflight
        return await asyncio.shield(inflight)

    async def _run_health_probe(self, provider_id: str, timeout: float) -> ProviderHealthSnapshot:
        try:
            strategy = self._registry.get_or_create_strategy(provider_id)
            if strategy is None:
                return ProviderHealthSnapshot(
                    provider_name=provider_id,
                    status="unknown",
                    message="Provider not found",
                    checked_at=time.monotonic(),
                    sample_count=self._health_samples.get(provider_id, 0),
                )
            started = time.monotonic()
            status = "unhealthy"
            message = "Provider health check failed"
            try:
                health = await asyncio.wait_for(asyncio.to_thread(strategy.check_health), timeout)
                if health is not None and getattr(health, "is_healthy", False):
                    status = "healthy"
                    message = "Provider is healthy"
                else:
                    self._logger.warning(
                        "Provider %s reported unhealthy: %s",
                        provider_id,
                        getattr(health, "status_message", None),
                    )
            except asyncio.TimeoutError:
                message = "Provider health check timed out"
                self._logger.warning(
                    "Health probe for provider %s timed out after %.1fs", provider_id, timeout
                )
            except Exception as exc:
                self._logger.warning("Health probe for provider %s failed: %s", provider_id, exc)
            elapsed_ms = (time.monotonic() - started) * 1000
            self._health_samples[provider_id] = self._health_samples.get(provider_id, 0) + 1
            snapshot = ProviderHealthSnapshot(
                provider_name=provider_id,
                status=status,
                message=message,
                response_time_ms=round(elapsed_ms, 1),
                sample_count=self._health_samples[provider_id],
                checked_at=time.monotonic(),
            )
            self._health_cache[provider_id] = snapshot
            return snapshot
        except Exception as exc:
            self._logger.warning("Health probe for provider %s errored: %s", provider_id, exc)
            return ProviderHealthSnapshot(
                provider_name=provider_id,
                status="unknown",
                message="Provider health could not be determined",
                checked_at=time.monotonic(),
            )
        finally:
            self._health_inflight.pop(provider_id, None)

    def update_provider_health(self, provider_name: str, health_data: dict) -> None:
        """Persist health state for a provider into the registry."""
        self._registry.update_provider_health(provider_name, health_data)

    def resolve_api_alias(self, provider_id: str, raw_api: str) -> str:
        """Resolve a provider API name to its canonical form via the named strategy.

        Returns raw_api unchanged if the strategy is not found.
        """
        strategy = self._registry.get_or_create_strategy(provider_id)
        if strategy is None:
            return raw_api
        return strategy.resolve_api_alias(raw_api)

    def get_registered_provider_types(self) -> list[str]:
        """Return registered provider type names."""
        return self._registry.get_registered_providers()

    def ensure_provider_registered(self, provider_type: str) -> bool:
        """Ensure a provider type is registered. Returns True on success."""
        return self._registry.ensure_provider_type_registered(provider_type)

    def get_or_create_strategy(self, provider_identifier: str, config: Any = None) -> Any:
        """Get or create a provider strategy instance."""
        return self._registry.get_or_create_strategy(provider_identifier, config)
