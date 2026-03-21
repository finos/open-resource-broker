"""Tests for register_monitoring_services() DI registration."""

from collections.abc import Generator
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from orb.bootstrap.monitoring_services import register_monitoring_services
from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.di.container import DIContainer
from orb.monitoring.health import HealthCheck


def _make_container() -> DIContainer:
    """Create a minimal container with only what monitoring_services needs."""
    container = DIContainer()
    mock_logger = Mock(spec=LoggingPort)
    container.register_instance(LoggingPort, mock_logger)
    return container


@pytest.fixture()
def container() -> Generator[DIContainer, None, None]:
    with (
        patch.object(Path, "mkdir"),
        patch(
            "orb.bootstrap.monitoring_services.get_health_location",
            return_value=Path("/tmp/orb-health-test"),
        ),
    ):
        c = _make_container()
        register_monitoring_services(c)
        yield c


def test_register_monitoring_services_registers_health_check_port(container: DIContainer) -> None:
    """get(HealthCheckPort) returns a HealthCheck instance."""
    with (
        patch.object(Path, "mkdir"),
        patch(
            "orb.bootstrap.monitoring_services.get_health_location",
            return_value=Path("/tmp/orb-health-test"),
        ),
    ):
        instance = container.get(HealthCheckPort)
    assert isinstance(instance, HealthCheck)


def test_register_monitoring_services_concrete_alias_is_same_instance(
    container: DIContainer,
) -> None:
    """get(HealthCheck) and get(HealthCheckPort) return the same instance."""
    with (
        patch.object(Path, "mkdir"),
        patch(
            "orb.bootstrap.monitoring_services.get_health_location",
            return_value=Path("/tmp/orb-health-test"),
        ),
    ):
        port_instance = container.get(HealthCheckPort)
        concrete_instance = container.get(HealthCheck)
    assert concrete_instance is port_instance
