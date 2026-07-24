"""Regression tests: init credential helpers must resolve the provider strategy.

These cover a defect where the credential-inquiry helpers
(``_get_available_credential_sources``, ``_test_provider_credentials``,
``_get_credential_requirements``, ``_get_operational_requirements``) called
``_get_provider_strategy`` WITHOUT threading the DI container.  Because
``_get_provider_strategy`` returns ``None`` when it has neither a registry nor
a container, every provider degraded to "Provider type not supported" during
``orb init`` — even though the strategy was perfectly resolvable.

The fix threads the container through each helper so the strategy resolves and
credential testing reaches the real provider auth call.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import orb.interface.init_command_handler as _mod
from orb.domain.base.ports.provider_registry_port import ProviderRegistryPort


def _container_resolving_strategy(strategy_class: MagicMock) -> MagicMock:
    """Build a DI container whose ProviderRegistryPort resolves ``strategy_class``.

    This mirrors the real init flow: the container is available, the registry
    is resolvable from it, and the strategy class is reachable via
    ``_get_type_registration``.
    """
    reg = MagicMock()
    reg.strategy_class = strategy_class
    # NOTE: a plain MagicMock (not spec=ProviderRegistryPort) is required because
    # _get_provider_strategy reaches the private ``_get_type_registration`` method,
    # which is not part of the public port protocol.
    registry = MagicMock()
    registry.ensure_provider_type_registered.return_value = True
    registry._get_type_registration.return_value = reg

    container = MagicMock()
    container.get.side_effect = lambda t: registry if t is ProviderRegistryPort else MagicMock()
    return container


@pytest.mark.unit
class TestCredentialHelpersResolveStrategyWithContainer:
    """Given a container, each helper must resolve the strategy (not degrade)."""

    def test_test_provider_credentials_resolves_strategy_not_unsupported(self) -> None:
        strategy = MagicMock()
        strategy.test_credentials.return_value = {"success": True}
        container = _container_resolving_strategy(strategy)

        ok, msg = _mod._test_provider_credentials("aws", None, container=container)

        assert ok is True
        assert "not supported" not in msg
        strategy.test_credentials.assert_called_once()

    def test_test_provider_credentials_surfaces_real_auth_error_not_unsupported(self) -> None:
        # A real credential failure must NOT be masked as "Provider type not supported".
        strategy = MagicMock()
        strategy.test_credentials.return_value = {
            "success": False,
            "error": "Unable to locate credentials",
        }
        container = _container_resolving_strategy(strategy)

        ok, msg = _mod._test_provider_credentials("aws", "default", container=container)

        assert ok is False
        assert "not supported" not in msg
        assert "Unable to locate credentials" in msg

    def test_get_available_credential_sources_uses_strategy(self) -> None:
        strategy = MagicMock()
        strategy.get_available_credential_sources.return_value = [
            {"name": "default", "description": "Default profile", "config_delta": {}},
        ]
        container = _container_resolving_strategy(strategy)

        sources = _mod._get_available_credential_sources("aws", container=container)

        assert sources[0]["name"] == "default"
        strategy.get_available_credential_sources.assert_called_once()

    def test_get_credential_requirements_uses_strategy(self) -> None:
        strategy = MagicMock()
        strategy.get_credential_requirements.return_value = {"tenant_id": {"required": True}}
        container = _container_resolving_strategy(strategy)

        reqs = _mod._get_credential_requirements("aws", container=container)

        assert reqs == {"tenant_id": {"required": True}}

    def test_get_operational_requirements_uses_strategy(self) -> None:
        strategy = MagicMock()
        strategy.get_operational_requirements.return_value = {
            "region": {"required": True, "description": "AWS region"}
        }
        container = _container_resolving_strategy(strategy)

        reqs = _mod._get_operational_requirements("aws", container=container)

        assert "region" in reqs


@pytest.mark.unit
class TestCredentialHelpersWithoutContainer:
    """Without a container the helpers must degrade gracefully (no crash)."""

    def test_test_provider_credentials_no_container_reports_unsupported(self) -> None:
        # This is the legitimate degraded path (no way to resolve the strategy).
        ok, msg = _mod._test_provider_credentials("aws", None)
        assert ok is False
        assert "not supported" in msg

    def test_credential_sources_no_container_returns_default(self) -> None:
        sources = _mod._get_available_credential_sources("aws")
        assert sources == [{"name": None, "description": "Default credentials"}]


@pytest.mark.unit
class TestAWSStrategyResolvesViaRealContainer:
    """End-to-end: the real DI container must resolve the AWS strategy class.

    This is the concrete guarantee that ``orb init`` works for aws up to the
    real credential call (which may legitimately fail without creds, but NOT
    with "Provider type not supported").
    """

    def test_real_container_resolves_aws_strategy(self) -> None:
        from orb.infrastructure.di.container import get_container
        from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy

        container = get_container()

        strategy_class = _mod._get_provider_strategy("aws", container=container)

        assert strategy_class is AWSProviderStrategy

    def test_real_container_test_credentials_reaches_provider_call(self) -> None:
        from orb.infrastructure.di.container import get_container

        container = get_container()

        ok, msg = _mod._test_provider_credentials("aws", None, container=container)

        # Whether creds exist depends on the environment; the invariant is that
        # the strategy WAS resolved, so the error is never the misleading
        # "Provider type not supported".
        assert "Provider type not supported" not in msg
        assert "not supported" not in msg
