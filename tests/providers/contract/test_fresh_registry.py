"""The ``registry`` fixture must not inherit registrations from earlier tests."""

from __future__ import annotations

import pytest

from orb.providers.aws import registration as aws_registration
from orb.providers.aws.provider_plugin import AWSPlugin
from orb.providers.registry import get_provider_registry
from tests.providers.contract.fresh_registry import freshly_registered_providers


def test_provider_that_fails_to_register_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = get_provider_registry()
    with freshly_registered_providers():
        # An earlier test on this worker registered every provider; keep that
        # state in place while the next registration attempt is made.
        earlier = dict(registry._type_registrations)
    assert "aws" in earlier
    monkeypatch.setattr(registry, "_type_registrations", dict(earlier))

    def _broken(_registry: object) -> None:
        raise ImportError("simulated missing dependency")

    # Both registration paths: the entry-point plugin and the built-in module.
    monkeypatch.setattr(AWSPlugin, "register_provider", _broken)
    monkeypatch.setattr(aws_registration, "register_aws_provider", _broken)
    with freshly_registered_providers() as populated:
        assert not populated.is_registered("aws")
        assert populated.is_registered("k8s")

    assert registry.is_registered("aws"), "earlier registrations are restored"
