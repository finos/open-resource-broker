"""Focused tests for AzureValidationAdapter."""

from unittest.mock import MagicMock

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.infrastructure.adapters.azure_validation_adapter import (
    AzureValidationAdapter,
)


def test_validate_template_configuration_uses_azure_rules():
    adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

    result = adapter.validate_template_configuration(
        {
            "provider_api": "VMSS",
            "template_id": "t1",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        }
    )

    assert result["valid"] is True
    assert result["errors"] == []
    assert "provider_api" in result["validated_fields"]


def test_validate_template_configuration_rejects_unsupported_provider_api():
    adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

    result = adapter.validate_template_configuration(
        {
            "provider_api": "BogusApi",
            "template_id": "t1",
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        }
    )

    assert result["valid"] is False
    assert any("Unsupported provider API" in error for error in result["errors"])


def test_validate_provider_api_fallback_accepts_vmss_uniform():
    adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

    assert adapter.validate_provider_api("VMSSUniform") is True


def test_validate_provider_api_does_not_accept_dead_config_only_api_names():
    adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

    assert adapter.validate_provider_api("AzureFleet") is False


def test_validate_template_configuration_rejects_spot_percentage_for_uniform():
    adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

    result = adapter.validate_template_configuration(
        {
            "provider_api": "VMSS",
            "template_id": "t1",
            "orchestration_mode": "Uniform",
            "spot_percentage": 70,
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        }
    )

    assert result["valid"] is False
    assert any(
        "spot_percentage requires Flexible orchestration mode" in error
        for error in result["errors"]
    )


def test_validate_template_configuration_accepts_spot_percentage_without_spot_priority():
    adapter = AzureValidationAdapter(config=AzureProviderConfig(), logger=MagicMock())

    result = adapter.validate_template_configuration(
        {
            "provider_api": "VMSS",
            "template_id": "t1",
            "spot_percentage": 70,
            "vm_size": "Standard_D4s_v5",
            "resource_group": "rg",
            "location": "eastus2",
            "ssh_public_keys": ["ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQC7 test@host"],
            "image": {"publisher": "C", "offer": "o", "sku": "s"},
        }
    )

    assert result["valid"] is True
    assert result["errors"] == []
