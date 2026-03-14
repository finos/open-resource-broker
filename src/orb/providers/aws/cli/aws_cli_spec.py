"""AWS-specific CLI argument specification."""

import argparse
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class AWSCLISpec:
    """CLI spec for the AWS provider."""

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add --aws-profile and --aws-region arguments to the parser."""
        parser.add_argument("--aws-profile", dest="aws_profile", help="AWS profile name")
        parser.add_argument("--aws-region", dest="aws_region", help="AWS region")

    def extract_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Return full config dict from args (all required fields)."""
        return {
            "profile": args.aws_profile,
            "region": args.aws_region,
        }

    def extract_partial_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Return only the fields that were explicitly provided (non-None)."""
        result: dict[str, Any] = {}
        if getattr(args, "aws_profile", None) is not None:
            result["profile"] = args.aws_profile
        if getattr(args, "aws_region", None) is not None:
            result["region"] = args.aws_region
        return result

    def validate_add(self, args: argparse.Namespace) -> list[str]:
        """Return error messages for missing required add fields."""
        errors: list[str] = []
        if not getattr(args, "aws_profile", None):
            errors.append("--aws-profile is required")
        if not getattr(args, "aws_region", None):
            errors.append("--aws-region is required")
        return errors

    def generate_name(self, args: argparse.Namespace) -> str:
        """Generate a provider instance name from AWS profile and region."""
        profile = getattr(args, "aws_profile", None) or ""
        region = getattr(args, "aws_region", None) or ""
        try:
            from orb.application.services.provider_registry_service import ProviderRegistryService
            from orb.infrastructure.di.container import get_container

            registry_service = get_container().get(ProviderRegistryService)
            temp_config = {"type": "aws", "profile": profile, "region": region}
            strategy = registry_service.get_or_create_strategy("aws", temp_config)
            if strategy is not None:
                return strategy.generate_provider_name({"profile": profile, "region": region})
        except Exception:
            # Registry lookup failed (e.g. container not initialised) — fall back to static name
            logger.debug("Failed to generate name via registry; using fallback", exc_info=True)
        sanitized_profile = re.sub(r"[^a-zA-Z0-9\-_]", "-", profile)
        return f"aws_{sanitized_profile}_{region}"

    def format_display(self, config: dict[str, Any]) -> list[tuple[str, str]]:
        """Return (label, value) pairs for display."""
        return [
            ("Profile", config.get("profile", "\u2014")),
            ("Region", config.get("region", "\u2014")),
        ]
