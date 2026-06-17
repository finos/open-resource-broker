"""Azure-specific CLI argument specification."""

import argparse
import re
from typing import Any


class AzureCLISpec:
    """CLI spec for the Azure provider."""

    @staticmethod
    def _arg(args: argparse.Namespace, name: str, default: Any = None) -> Any:
        """Read a parsed CLI argument by name."""
        return vars(args).get(name, default)

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add Azure provider arguments to the parser."""
        parser.add_argument(
            "--azure-subscription-id",
            dest="azure_subscription_id",
            help="Azure subscription ID",
        )
        parser.add_argument(
            "--azure-resource-group",
            dest="azure_resource_group",
            help="Azure resource group",
        )
        parser.add_argument(
            "--azure-location",
            dest="azure_location",
            help="Azure location",
        )
        parser.add_argument(
            "--azure-client-id",
            dest="azure_client_id",
            help="Managed identity client ID",
        )
        parser.add_argument(
            "--azure-cyclecloud-url",
            dest="azure_cyclecloud_url",
            help="CycleCloud URL",
        )
        parser.add_argument(
            "--azure-cyclecloud-credential-path",
            dest="azure_cyclecloud_credential_path",
            help="Secret path or file path for CycleCloud credentials",
        )
        parser.add_argument(
            "--azure-cyclecloud-auth-mode",
            dest="azure_cyclecloud_auth_mode",
            help="CycleCloud auth mode override",
        )
        parser.add_argument(
            "--azure-cyclecloud-aad-scope",
            dest="azure_cyclecloud_aad_scope",
            help="CycleCloud AAD scope override",
        )
        parser.add_argument(
            "--azure-cyclecloud-verify-ssl",
            dest="azure_cyclecloud_verify_ssl",
            action="store_true",
            help="Verify TLS certificates for CycleCloud",
        )
        parser.add_argument(
            "--azure-cyclecloud-no-verify-ssl",
            dest="azure_cyclecloud_no_verify_ssl",
            action="store_true",
            help="Disable TLS certificate verification for CycleCloud",
        )

    def extract_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Return full config dict from args."""
        result: dict[str, Any] = {
            "subscription_id": self._arg(args, "azure_subscription_id"),
            "resource_group": self._arg(args, "azure_resource_group"),
            "region": self._arg(args, "azure_location") or "eastus2",
        }

        client_id = self._arg(args, "azure_client_id")
        if client_id:
            result["client_id"] = client_id

        cyclecloud = self._extract_cyclecloud_config(args, partial=False)
        if cyclecloud:
            result["cyclecloud"] = cyclecloud

        return result

    def extract_partial_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Return only explicitly provided Azure config fields."""
        result: dict[str, Any] = {}

        if self._arg(args, "azure_subscription_id") is not None:
            result["subscription_id"] = args.azure_subscription_id
        if self._arg(args, "azure_resource_group") is not None:
            result["resource_group"] = args.azure_resource_group
        if self._arg(args, "azure_location") is not None:
            result["region"] = args.azure_location
        if self._arg(args, "azure_client_id") is not None:
            result["client_id"] = args.azure_client_id

        cyclecloud = self._extract_cyclecloud_config(args, partial=True)
        if cyclecloud:
            result["cyclecloud"] = cyclecloud

        return result

    def validate_add(self, args: argparse.Namespace) -> list[str]:
        """Return error messages for missing required add fields."""
        errors: list[str] = []
        if not self._arg(args, "azure_subscription_id"):
            errors.append("--azure-subscription-id is required")
        if not self._arg(args, "azure_resource_group"):
            errors.append("--azure-resource-group is required")
        return errors

    def generate_name(self, args: argparse.Namespace) -> str:
        """Generate a provider instance name from Azure subscription and location."""
        try:
            subscription_id = self._arg(args, "azure_subscription_id") or "default"
            location = self._arg(args, "azure_location") or "eastus2"
            sanitized_subscription = re.sub(r"[^a-zA-Z0-9\-_]", "-", subscription_id)
            return f"azure_{sanitized_subscription}_{location}"
        except Exception:
            pass
        return "azure_default"

    def format_display(self, config: dict[str, Any]) -> list[tuple[str, str]]:
        """Return (label, value) pairs for display."""
        cyclecloud = config.get("cyclecloud") or {}
        return [
            ("Subscription", config.get("subscription_id", "-")),
            ("Resource Group", config.get("resource_group", "-")),
            ("Location", config.get("region", config.get("location", "-"))),
            ("Client ID", config.get("client_id", "-")),
            ("CycleCloud URL", cyclecloud.get("url", "-")),
            ("CycleCloud Credential Path", cyclecloud.get("credential_path", "-")),
        ]

    def _extract_cyclecloud_config(
        self, args: argparse.Namespace, *, partial: bool
    ) -> dict[str, Any]:
        """Extract nested CycleCloud config from CLI args."""
        cyclecloud: dict[str, Any] = {}

        field_map = {
            "url": "azure_cyclecloud_url",
            "credential_path": "azure_cyclecloud_credential_path",
            "auth_mode": "azure_cyclecloud_auth_mode",
            "aad_scope": "azure_cyclecloud_aad_scope",
        }
        for config_key, arg_name in field_map.items():
            value = self._arg(args, arg_name)
            if value is not None:
                cyclecloud[config_key] = value

        verify_ssl = self._extract_verify_ssl(args)
        if verify_ssl is not None:
            cyclecloud["verify_ssl"] = verify_ssl

        if partial:
            return cyclecloud

        return {k: v for k, v in cyclecloud.items() if v is not None}

    @staticmethod
    def _extract_verify_ssl(args: argparse.Namespace) -> bool | None:
        """Resolve CycleCloud TLS verification flags."""
        verify_ssl = vars(args).get("azure_cyclecloud_verify_ssl", False)
        no_verify_ssl = vars(args).get("azure_cyclecloud_no_verify_ssl", False)
        if verify_ssl and no_verify_ssl:
            raise ValueError(
                "Cannot specify both --azure-cyclecloud-verify-ssl and "
                "--azure-cyclecloud-no-verify-ssl"
            )
        if verify_ssl:
            return True
        if no_verify_ssl:
            return False
        return None
