"""Monitoring service registrations for dependency injection."""

from orb.config.platform_dirs import get_health_location
from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.storage_port import StoragePort
from orb.infrastructure.logging.logger import get_logger
from orb.monitoring.health import HealthCheck, HealthCheckConfig, register_storage_health_checks


def register_monitoring_services(container) -> None:
    """Register monitoring services with the DI container.

    HealthCheck is a cross-cutting concern and belongs here, not in the
    AWS provider registration module. After registering HealthCheck we
    install a provider-agnostic ``database`` check that delegates to the
    active StoragePort's ``is_healthy`` method, replacing the legacy
    ``unknown`` placeholder for json/sql/dynamodb alike.
    """

    def _create_health_check(c) -> HealthCheck:
        config = HealthCheckConfig(health_dir=get_health_location())
        hc = HealthCheck(
            config=config,
            logger=c.get(LoggingPort),
        )
        # Wire storage health into the application's HealthCheck the first
        # time it's resolved. Done here (not from storage_services) so the
        # default ``unknown`` placeholder is replaced for every deployment,
        # not just those that go through the AWS provider path.
        try:
            storage = c.get(StoragePort)
        except Exception as exc:
            get_logger(__name__).debug("StoragePort not available for health wiring: %s", exc)
            return hc
        register_storage_health_checks(hc, storage)
        return hc

    container.register_singleton(HealthCheckPort, _create_health_check)
    # Also register the concrete class so existing callers using HealthCheck directly still work
    container.register_singleton(HealthCheck, lambda c: c.get(HealthCheckPort))
