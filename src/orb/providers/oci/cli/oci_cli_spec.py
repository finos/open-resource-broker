"""OCI-specific CLI argument specification."""

from __future__ import annotations

import argparse
import re
from typing import Any


class OCICLISpec:
    """CLI spec for the OCI provider."""

    _AUTH_MODES = {
        "api_key",
        "default",
        "instance_principal",
        "profile",
        "resource_principal",
    }
    _API_KEY_FIELDS = (
        "oci_tenancy_ocid",
        "oci_user_ocid",
        "oci_fingerprint",
        "oci_private_key_path",
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add OCI provider-specific arguments to the parser."""
        parser.add_argument("--oci-region", dest="oci_region", help="OCI region")
        parser.add_argument("--oci-profile", dest="oci_profile", help="OCI config profile")
        parser.add_argument(
            "--oci-credential-source",
            dest="oci_credential_source",
            choices=sorted(self._AUTH_MODES),
            help="OCI credential source",
        )
        parser.add_argument("--oci-tenancy-ocid", dest="oci_tenancy_ocid", help="OCI tenancy OCID")
        parser.add_argument("--oci-user-ocid", dest="oci_user_ocid", help="OCI user OCID")
        parser.add_argument("--oci-fingerprint", dest="oci_fingerprint", help="OCI fingerprint")
        parser.add_argument(
            "--oci-private-key-path",
            dest="oci_private_key_path",
            help="OCI API private key path",
        )

    def extract_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Return full config dict from add args."""
        result: dict[str, Any] = {"region": args.oci_region}
        self._copy_if_present(result, "profile", getattr(args, "oci_profile", None))
        self._copy_if_present(
            result,
            "credential_source",
            getattr(args, "oci_credential_source", None),
        )
        self._copy_if_present(result, "tenancy_ocid", getattr(args, "oci_tenancy_ocid", None))
        self._copy_if_present(result, "user_ocid", getattr(args, "oci_user_ocid", None))
        self._copy_if_present(result, "fingerprint", getattr(args, "oci_fingerprint", None))
        self._copy_if_present(
            result,
            "private_key_path",
            getattr(args, "oci_private_key_path", None),
        )
        return result

    def extract_partial_config(self, args: argparse.Namespace) -> dict[str, Any]:
        """Return only OCI config fields explicitly supplied for update."""
        result: dict[str, Any] = {}
        field_map = {
            "oci_region": "region",
            "oci_profile": "profile",
            "oci_credential_source": "credential_source",
            "oci_tenancy_ocid": "tenancy_ocid",
            "oci_user_ocid": "user_ocid",
            "oci_fingerprint": "fingerprint",
            "oci_private_key_path": "private_key_path",
        }
        for attr, key in field_map.items():
            if getattr(args, attr, None) is not None:
                result[key] = getattr(args, attr)
        return result

    def validate_add(self, args: argparse.Namespace) -> list[str]:
        """Return validation errors for OCI provider add args."""
        errors: list[str] = []
        region = getattr(args, "oci_region", None)
        profile = getattr(args, "oci_profile", None)
        source = getattr(args, "oci_credential_source", None)

        if not region:
            errors.append("--oci-region is required")

        if source == "profile" and not profile:
            errors.append("--oci-profile is required when --oci-credential-source=profile")

        if source == "api_key":
            for attr in self._API_KEY_FIELDS:
                if not getattr(args, attr, None):
                    flag = "--" + attr.replace("_", "-")
                    errors.append(f"{flag} is required when --oci-credential-source=api_key")

        if source is None and not profile and not self._has_complete_api_key_args(args):
            errors.append(
                "provide --oci-profile, --oci-credential-source, or complete OCI API key fields"
            )

        return errors

    def generate_name(self, args: argparse.Namespace) -> str:
        """Generate provider name from OCI profile/source and region."""
        try:
            profile_or_source = (
                getattr(args, "oci_profile", None)
                or getattr(args, "oci_credential_source", None)
                or "default"
            )
            region = getattr(args, "oci_region", None) or "us-phoenix-1"
            sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "-", profile_or_source)
            return f"oci_{sanitized}_{region}"
        except Exception:
            return "oci_default"

    def format_display(self, config: dict[str, Any]) -> list[tuple[str, str]]:
        """Return (label, value) pairs for display."""
        return [
            ("Region", str(config.get("region", "-"))),
            ("Credential Source", str(config.get("credential_source", "-"))),
            ("Profile", str(config.get("profile", "-"))),
            ("Tenancy OCID", self._redact(config.get("tenancy_ocid"))),
            ("User OCID", self._redact(config.get("user_ocid"))),
            ("Private Key Path", str(config.get("private_key_path", "-"))),
        ]

    @staticmethod
    def _copy_if_present(target: dict[str, Any], key: str, value: Any) -> None:
        if value is not None:
            target[key] = value

    @classmethod
    def _has_complete_api_key_args(cls, args: argparse.Namespace) -> bool:
        return all(getattr(args, attr, None) for attr in cls._API_KEY_FIELDS)

    @staticmethod
    def _redact(value: Any) -> str:
        if not value:
            return "-"
        text = str(value)
        if len(text) <= 12:
            return "***"
        return f"{text[:8]}...{text[-4:]}"
