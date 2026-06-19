"""GCP-specific CLI argument specification."""

from __future__ import annotations

import argparse
import re
from typing import Any


class GCPCLISpec:
    """CLI spec for the GCP provider."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register GCP-specific CLI flags."""
        parser.add_argument("--gcp-project-id", dest="gcp_project_id", help="GCP project ID")
        parser.add_argument("--gcp-region", dest="gcp_region", help="GCP region")
        parser.add_argument(
            "--gcp-zones",
            dest="gcp_zones",
            help="Comma-separated GCP zones for preferred placement",
        )
        parser.add_argument("--gcp-network", dest="gcp_network", help="Default VPC network")
        parser.add_argument("--gcp-subnetwork", dest="gcp_subnetwork", help="Default subnetwork")
        parser.add_argument(
            "--gcp-service-account-email",
            dest="gcp_service_account_email",
            help="Default service account email",
        )
        parser.add_argument(
            "--gcp-service-account-scopes",
            dest="gcp_service_account_scopes",
            help="Comma-separated OAuth scopes for the attached service account",
        )

    def extract_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Build a provider config payload from required CLI arguments."""
        config = {
            "project_id": args.gcp_project_id,
            "region": args.gcp_region,
        }
        if args.gcp_zones:
            config["zones"] = [zone.strip() for zone in args.gcp_zones.split(",") if zone.strip()]
        if args.gcp_network:
            config["network"] = args.gcp_network
        if args.gcp_subnetwork:
            config["subnetwork"] = args.gcp_subnetwork
        return config

    def extract_partial_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Build a partial provider config payload from explicitly supplied CLI values."""
        result: dict[str, Any] = {}
        if args.gcp_project_id is not None:
            result["project_id"] = args.gcp_project_id
        if args.gcp_region is not None:
            result["region"] = args.gcp_region
        if args.gcp_zones is not None:
            result["zones"] = [zone.strip() for zone in args.gcp_zones.split(",") if zone.strip()]
        if args.gcp_network is not None:
            result["network"] = args.gcp_network
        if args.gcp_subnetwork is not None:
            result["subnetwork"] = args.gcp_subnetwork
        return result

    def validate_add(self, args: argparse.Namespace) -> list[str]:
        """Return validation errors for `orb provider add`."""
        errors: list[str] = []
        if not args.gcp_project_id:
            errors.append("--gcp-project-id is required")
        if not args.gcp_region:
            errors.append("--gcp-region is required")
        return errors

    def generate_name(self, args: argparse.Namespace) -> str:
        """Generate a stable provider name from the selected project and region."""
        project_id = args.gcp_project_id or "default"
        region = args.gcp_region or "global"
        sanitized_project = re.sub(r"[^a-zA-Z0-9\-_]", "-", project_id)
        return f"gcp_{sanitized_project}_{region}"

    def format_display(self, config: dict[str, Any]) -> list[tuple[str, str]]:
        """Format provider config values for CLI display."""
        zones = config.get("zones") or []
        return [
            ("Project", config.get("project_id", "-")),
            ("Region", config.get("region", "-")),
            ("Zones", ", ".join(zones) if zones else "-"),
            ("Network", config.get("network", "-")),
            ("Subnetwork", config.get("subnetwork", "-")),
            ("Auth", "ADC"),
        ]
