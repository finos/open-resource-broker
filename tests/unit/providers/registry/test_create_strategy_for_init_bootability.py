"""Tests for classmethod bootability — credential and region inquiry without a strategy instance.

Previously this file tested ``create_strategy_for_init`` bootability.  It now
guards that the credential/region classmethods on both the AWS and k8s
strategy classes are directly callable without constructing an instance.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Test 1: K8s classmethod get_available_credential_sources is callable
# ---------------------------------------------------------------------------


def test_k8s_get_available_credential_sources_callable_on_class() -> None:
    """K8sProviderStrategy.get_available_credential_sources must be callable on the class.

    Uses the real implementation so the test exercises the actual production
    code path rather than a stub.  The DI container is patched to raise
    immediately so the method's optional DI lookups fail gracefully without
    polluting other tests.
    """
    from unittest.mock import patch

    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    with patch(
        "orb.infrastructure.di.container.get_container",
        side_effect=Exception("DI not ready"),
    ):
        sources = K8sProviderStrategy.get_available_credential_sources()

    assert isinstance(sources, list), (
        "get_available_credential_sources must return a list, got %r" % type(sources)
    )
    for entry in sources:
        assert "name" in entry, "Each credential source must have a 'name' key"


# ---------------------------------------------------------------------------
# Test 2: K8s classmethod get_default_region returns empty string
# ---------------------------------------------------------------------------


def test_k8s_get_default_region_callable_on_class() -> None:
    """K8sProviderStrategy.get_default_region must return '' when called on the class."""
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    assert K8sProviderStrategy.get_default_region() == ""


# ---------------------------------------------------------------------------
# Test 3: K8s classmethod generate_provider_name callable on class
# ---------------------------------------------------------------------------


def test_k8s_generate_provider_name_callable_on_class() -> None:
    """K8sProviderStrategy.generate_provider_name must be callable on the class."""
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

    name = K8sProviderStrategy.generate_provider_name({"context": "prod-cluster"})
    assert name == "k8s_prod-cluster"


# ---------------------------------------------------------------------------
# Test 4: AWS classmethod get_available_credential_sources is callable
# ---------------------------------------------------------------------------


def test_aws_get_available_credential_sources_callable_on_class() -> None:
    """AWSProviderStrategy.get_available_credential_sources must be callable on the class."""
    from unittest.mock import patch

    from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

    # Patch profile_discovery so the test does not depend on ~/.aws/credentials
    with patch(
        "orb.providers.aws.profile_discovery.get_available_profiles",
        return_value=[{"name": "default", "description": "default AWS credentials"}],
    ):
        sources = AWSProviderStrategy.get_available_credential_sources()

    assert isinstance(sources, list)
    assert len(sources) >= 1


# ---------------------------------------------------------------------------
# Test 5: AWS classmethod get_default_region returns 'us-east-1'
# ---------------------------------------------------------------------------


def test_aws_get_default_region_callable_on_class() -> None:
    """AWSProviderStrategy.get_default_region must return 'us-east-1' when called on the class."""
    from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

    assert AWSProviderStrategy.get_default_region() == "us-east-1"


# ---------------------------------------------------------------------------
# Test 6: AWS classmethod generate_provider_name callable on class
# ---------------------------------------------------------------------------


def test_aws_generate_provider_name_callable_on_class() -> None:
    """AWSProviderStrategy.generate_provider_name must be callable on the class."""
    from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

    name = AWSProviderStrategy.generate_provider_name(
        {"profile": "my-profile", "region": "eu-west-1"}
    )
    assert name == "aws_my-profile_eu-west-1"
