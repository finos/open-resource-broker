"""Unit tests for _register_ami_resolver_if_enabled guard.

Verifies that the AMICacheService + AWSAMIResolver are only wired into the
DI container when the ``aws`` provider is present in ``_REGISTERED_PROVIDERS``.
A k8s-only deployment must not get an AWS-backed ImageResolver registered.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from orb.infrastructure.di.container import DIContainer


def _call_register(registered_providers: list[str]) -> DIContainer:
    """Call _register_ami_resolver_if_enabled with a patched provider list."""
    from orb.bootstrap.infrastructure_services import _register_ami_resolver_if_enabled

    container = DIContainer()
    # The function does ``from orb.providers.registration import _REGISTERED_PROVIDERS``
    # at call time; patch the module-level name at its definition site.
    with patch("orb.providers.registration._REGISTERED_PROVIDERS", registered_providers):
        _register_ami_resolver_if_enabled(container)
    return container


@pytest.mark.unit
class TestAMIResolverGuard:
    """Guard: AMI resolver is registered only when aws is a configured provider."""

    def test_aws_present_registers_image_resolver(self):
        """When aws is in the provider list, ImageResolver is registered."""
        from orb.domain.template.image_resolver import ImageResolver

        container = _call_register(["aws"])

        assert container.is_registered(ImageResolver), (
            "ImageResolver should be registered when aws provider is present"
        )

    def test_k8s_only_does_not_register_image_resolver(self):
        """When only k8s is configured, ImageResolver must NOT be registered."""
        from orb.domain.template.image_resolver import ImageResolver

        container = _call_register(["k8s"])

        assert not container.is_registered(ImageResolver), (
            "ImageResolver must not be registered for a k8s-only deployment "
            "(it would pull in boto3 and fail without AWS credentials)"
        )

    def test_empty_provider_list_does_not_register_image_resolver(self):
        """With no providers configured, ImageResolver must NOT be registered."""
        from orb.domain.template.image_resolver import ImageResolver

        container = _call_register([])

        assert not container.is_registered(ImageResolver)

    def test_aws_and_k8s_registers_image_resolver(self):
        """When both aws and k8s are configured, ImageResolver is registered."""
        from orb.domain.template.image_resolver import ImageResolver

        container = _call_register(["aws", "k8s"])

        assert container.is_registered(ImageResolver)
