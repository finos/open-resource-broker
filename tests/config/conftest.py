"""Shared fixtures for configuration tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def aws_provider_settings_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register AWSProviderConfig in ProviderSettingsRegistry for one test.

    Provider settings classes are registered during provider initialization
    rather than at import time, so tests calling
    ``ConfigTypeConverter.get_typed(AWSProviderConfig)`` directly must
    populate the registry explicitly instead of relying on shared state left
    behind by an earlier test.
    """
    from orb.config.schemas.provider_settings_registry import ProviderSettingsRegistry
    from orb.providers.aws.configuration.config import AWSProviderConfig

    monkeypatch.setitem(ProviderSettingsRegistry._settings_classes, "aws", AWSProviderConfig)
