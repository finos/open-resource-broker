"""Unit tests for init_command_handler — flow order and cache bypass."""

from unittest.mock import MagicMock, patch

import orb.interface.init_command_handler as _mod


def _make_strategy(regions=None, default_region="us-east-1"):
    strategy = MagicMock()
    strategy.get_available_regions.return_value = regions or []
    strategy.get_default_region.return_value = default_region
    strategy.get_available_credential_sources.return_value = [
        {"name": "default", "description": "Default profile"}
    ]
    strategy.test_credentials.return_value = {"success": True}
    strategy.get_credential_requirements.return_value = {}
    strategy.get_operational_requirements.return_value = {"region": {"required": True, "description": "AWS region"}}
    return strategy


def _mock_container():
    console = MagicMock()
    container = MagicMock()
    container.get.return_value = console
    return container


def test_discover_infrastructure_uses_fresh_strategy():
    """_discover_infrastructure must call create_strategy_by_type, not get_or_create_strategy."""
    mock_registry = MagicMock()
    mock_registry.ensure_provider_type_registered.return_value = True
    mock_strategy = MagicMock()
    mock_strategy.discover_infrastructure_interactive.return_value = {"vpc_id": "vpc-123"}
    mock_registry.create_strategy_by_type.return_value = mock_strategy

    with patch(
        "orb.providers.registry.provider_registry.ProviderRegistry",
    ), patch(
        "orb.providers.registry.get_provider_registry",
        return_value=mock_registry,
    ), patch(
        "orb.interface.init_command_handler.get_container",
        return_value=_mock_container(),
    ):
        result = _mod._discover_infrastructure("aws", "us-east-1", "my-profile")

    mock_registry.create_strategy_by_type.assert_called_once_with(
        "aws", {"region": "us-east-1", "profile": "my-profile"}
    )
    mock_registry.get_or_create_strategy.assert_not_called()
    assert result == {"vpc_id": "vpc-123"}


def test_get_operational_requirements_returns_region_for_aws():
    """_get_operational_requirements returns region dict when strategy provides it."""
    strategy = MagicMock()
    strategy.get_operational_requirements.return_value = {
        "region": {"required": True, "description": "AWS region"}
    }

    with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
        result = _mod._get_operational_requirements("aws")

    assert "region" in result
    assert result["region"]["required"] is True


def test_get_credential_requirements_returns_empty_for_aws():
    """_get_credential_requirements returns {} for AWS (no pre-auth params needed)."""
    strategy = MagicMock()
    strategy.get_credential_requirements.return_value = {}

    with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
        result = _mod._get_credential_requirements("aws")

    assert result == {}


def test_operational_requirements_separate_from_credential_requirements():
    """get_operational_requirements and get_credential_requirements return different things."""
    strategy = _make_strategy()

    with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
        cred_reqs = _mod._get_credential_requirements("aws")
        op_reqs = _mod._get_operational_requirements("aws")

    assert cred_reqs == {}
    assert "region" in op_reqs
    assert cred_reqs != op_reqs
