"""Provider-owned OCI command-line options."""

import argparse
from typing import Any


class OCICLISpec:
    FIELDS = (
        "region",
        "profile",
        "credential_source",
        "tenancy_ocid",
        "user_ocid",
        "fingerprint",
        "private_key_path",
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        for field in self.FIELDS:
            options: dict[str, Any] = {
                "dest": f"oci_{field}",
                "help": f"OCI {field.replace('_', ' ')}",
            }
            if field == "credential_source":
                options["choices"] = [
                    "default",
                    "profile",
                    "api_key",
                    "security_token",
                    "instance_principal",
                    "resource_principal",
                ]
            parser.add_argument("--oci-" + field.replace("_", "-"), **options)

    def extract_partial_config(self, args: argparse.Namespace) -> dict[str, Any]:
        return {
            field: vars(args)[f"oci_{field}"]
            for field in self.FIELDS
            if vars(args).get(f"oci_{field}") is not None
        }

    def extract_config(self, args: argparse.Namespace) -> dict[str, Any]:
        return {"region": "us-phoenix-1", **self.extract_partial_config(args)}

    def validate_add(self, args: argparse.Namespace) -> list[str]:
        from orb.providers.oci.configuration.config import OCIProviderConfig

        valid, message, missing, _ = OCIProviderConfig(
            **self.extract_config(args)
        ).validate_auth_configuration()
        return [] if valid else [f"{message}: {', '.join(missing)}"]

    def generate_name(self, args: argparse.Namespace) -> str:
        from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy

        return OCIProviderStrategy.generate_provider_name(self.extract_config(args))

    def format_display(self, config: dict[str, Any]) -> list[tuple[str, str]]:
        return [
            (field.replace("_", " ").title(), str(config.get(field) or "-"))
            for field in ("region", "profile", "credential_source")
        ]
