"""Contract tests for Azure CLI configuration mapping."""

import argparse

import pytest

from orb.providers.azure.cli.azure_cli_spec import AzureCLISpec


@pytest.fixture
def spec() -> AzureCLISpec:
    return AzureCLISpec()


@pytest.fixture
def parser(spec: AzureCLISpec) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    spec.add_arguments(parser)
    return parser


def test_full_config_maps_cli_names_and_applies_location_default(
    spec: AzureCLISpec,
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(
        [
            "--azure-subscription-id",
            "12345678-1234-1234-1234-123456789012",
            "--azure-resource-group",
            "workers-rg",
            "--azure-client-id",
            "managed-identity-id",
        ]
    )

    assert spec.extract_config(args) == {
        "subscription_id": "12345678-1234-1234-1234-123456789012",
        "resource_group": "workers-rg",
        "region": "eastus2",
        "client_id": "managed-identity-id",
    }


def test_partial_config_omits_unspecified_fields_and_preserves_explicit_false(
    spec: AzureCLISpec,
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(
        [
            "--azure-location",
            "uksouth",
            "--azure-cyclecloud-url",
            "https://cyclecloud.example.test",
            "--azure-cyclecloud-credential-path",
            "secret/cyclecloud",
            "--azure-cyclecloud-auth-mode",
            "bearer",
            "--azure-cyclecloud-aad-scope",
            "api://cyclecloud/.default",
            "--azure-cyclecloud-no-verify-ssl",
        ]
    )

    assert spec.extract_partial_config(args) == {
        "region": "uksouth",
        "cyclecloud": {
            "url": "https://cyclecloud.example.test",
            "credential_path": "secret/cyclecloud",
            "auth_mode": "bearer",
            "aad_scope": "api://cyclecloud/.default",
            "verify_ssl": False,
        },
    }


def test_full_config_preserves_explicit_cyclecloud_tls_verification(
    spec: AzureCLISpec,
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(["--azure-cyclecloud-verify-ssl"])

    assert spec.extract_config(args)["cyclecloud"] == {"verify_ssl": True}


def test_conflicting_cyclecloud_tls_flags_are_rejected(
    spec: AzureCLISpec,
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(["--azure-cyclecloud-verify-ssl", "--azure-cyclecloud-no-verify-ssl"])

    with pytest.raises(ValueError, match="Cannot specify both"):
        spec.extract_partial_config(args)


def test_validate_add_reports_each_missing_required_field(spec: AzureCLISpec) -> None:
    assert spec.validate_add(argparse.Namespace()) == [
        "--azure-subscription-id is required",
        "--azure-resource-group is required",
    ]
    assert (
        spec.validate_add(
            argparse.Namespace(
                azure_subscription_id="12345678-1234-1234-1234-123456789012",
                azure_resource_group="workers-rg",
            )
        )
        == []
    )


def test_generated_name_is_stable_and_safe_for_special_subscription_ids(
    spec: AzureCLISpec,
) -> None:
    args = argparse.Namespace(
        azure_subscription_id="tenant/subscription@example.test",
        azure_location="uksouth",
    )

    assert spec.generate_name(args) == "azure_tenant-subscription-example-test_uksouth"
    assert spec.generate_name(argparse.Namespace()) == "azure_default_eastus2"


def test_display_uses_canonical_region_and_readable_missing_value(
    spec: AzureCLISpec,
) -> None:
    display = dict(
        spec.format_display(
            {
                "subscription_id": "subscription-id",
                "resource_group": "workers-rg",
                "region": "uksouth",
                "location": "ignored-location-alias",
                "cyclecloud": {"credential_path": "secret/cyclecloud"},
            }
        )
    )

    assert display == {
        "Subscription": "subscription-id",
        "Resource Group": "workers-rg",
        "Location": "uksouth",
        "Client ID": "-",
        "CycleCloud URL": "-",
        "CycleCloud Credential Path": "secret/cyclecloud",
    }
