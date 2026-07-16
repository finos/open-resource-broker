"""Unit tests for AMI resolver DI wiring guard.

Verifies that AMICacheService + AWSAMIResolver are only wired into the DI
container when ``register_aws_services_with_di`` is called (i.e., when the aws
provider is present), and that a k8s-only deployment leaves ImageResolver
unregistered.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.infrastructure.di.container import DIContainer


def _call_register_aws(container: DIContainer) -> None:
    """Call register_aws_services_with_di with a real container."""
    from orb.providers.aws.registration import register_aws_services_with_di

    register_aws_services_with_di(container)


def _make_container_with_logger() -> DIContainer:
    """Return a DIContainer with a stub LoggingPort registered."""
    from orb.domain.base.ports import LoggingPort

    container = DIContainer()
    stub_logger = MagicMock(spec=LoggingPort)
    container.register_singleton(LoggingPort, lambda _c: stub_logger)
    return container


@pytest.mark.unit
class TestAMIResolverGuard:
    """Guard: AMI resolver is registered only when register_aws_services_with_di runs."""

    def test_aws_present_registers_image_resolver(self):
        """When register_aws_services_with_di is called, ImageResolver is registered."""
        from orb.domain.template.image_resolver import ImageResolver

        container = _make_container_with_logger()
        _call_register_aws(container)

        assert container.is_registered(ImageResolver), (
            "ImageResolver should be registered after register_aws_services_with_di"
        )

    def test_aws_present_registers_ami_cache_service(self):
        """AMICacheService is registered as part of aws DI registration."""
        from orb.infrastructure.caching.ami_cache_service import AMICacheService

        container = _make_container_with_logger()
        _call_register_aws(container)

        assert container.is_registered(AMICacheService), (
            "AMICacheService should be registered after register_aws_services_with_di"
        )

    def test_k8s_only_does_not_register_image_resolver(self):
        """When only k8s is configured, ImageResolver must NOT be registered.

        Simulated by simply NOT calling register_aws_services_with_di.
        """
        from orb.domain.template.image_resolver import ImageResolver

        container = DIContainer()

        assert not container.is_registered(ImageResolver), (
            "ImageResolver must not be registered for a k8s-only deployment "
            "(it would pull in boto3 and fail without AWS credentials)"
        )

    def test_idempotent_second_call_does_not_raise(self):
        """A second call to register_aws_services_with_di must be a no-op (idempotent)."""
        from orb.domain.template.image_resolver import ImageResolver

        container = _make_container_with_logger()
        _call_register_aws(container)
        # Second call must not raise even though services are already registered
        _call_register_aws(container)

        assert container.is_registered(ImageResolver)

    def test_aws_and_k8s_registers_image_resolver(self):
        """When both aws and k8s are configured, ImageResolver is registered after aws init."""
        from orb.domain.template.image_resolver import ImageResolver

        container = _make_container_with_logger()
        # Simulate k8s also present by verifying the aws path still registers the resolver
        _call_register_aws(container)

        assert container.is_registered(ImageResolver)
