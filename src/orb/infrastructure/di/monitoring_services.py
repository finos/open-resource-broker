"""Monitoring service registrations for dependency injection."""

import logging

from orb.config.platform_dirs import get_health_location
from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.monitoring.health import HealthCheck
from orb.monitoring.health import HealthCheckConfig


def register_monitoring_services(container) -> None:
    """Register monitoring services with the DI container.

    HealthCheck is a cross-cutting concern and belongs here, not in the
    AWS provider registration module.
    """

    def _create_health_check(c) -> HealthCheck:
        config = HealthCheckConfig(health_dir=get_health_location())
        return HealthCheck(
            config=config,
            logger=logging.getLogger("orb.monitoring.health"),
        )

    container.register_singleton(HealthCheckPort, _create_health_check)
    # Also register the concrete class so existing callers using HealthCheck directly still work
    container.register_singleton(HealthCheck, lambda c: c.get(HealthCheckPort))
