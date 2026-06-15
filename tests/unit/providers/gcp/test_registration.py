"""Tests for GCP provider registration."""

from unittest.mock import MagicMock


def test_create_gcp_strategy_builds_initialized_strategy() -> None:
    from orb.providers.gcp.registration import create_gcp_strategy

    strategy = create_gcp_strategy(
        {
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": ["us-central1-a"],
        }
    )

    assert strategy.is_initialized is True


def test_register_gcp_provider_registers_cli_spec() -> None:
    from orb.providers.gcp.registration import register_gcp_provider
    from orb.providers.registry import get_provider_registry

    registry = get_provider_registry()
    registry.clear_registrations()

    register_gcp_provider(registry=registry, logger=MagicMock())

    assert registry.is_provider_registered("gcp") is True
