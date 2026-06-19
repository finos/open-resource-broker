"""Tests for domain service registration."""

from unittest.mock import MagicMock

from orb.application.services.provider_validation_service import ProviderValidationService
from orb.domain.base.ports import ContainerPort, LoggingPort, ProviderSelectionPort
from orb.infrastructure.di.container import DIContainer


def test_provider_validation_service_is_not_prewired_to_aws_validator():
    from orb.bootstrap.domain_services import register_domain_services

    container = DIContainer()
    container.register_instance(ContainerPort, container)
    container.register_instance(LoggingPort, MagicMock())
    container.register_instance(ProviderSelectionPort, MagicMock())

    register_domain_services(container)

    service = container.get(ProviderValidationService)

    assert service._validator is None
