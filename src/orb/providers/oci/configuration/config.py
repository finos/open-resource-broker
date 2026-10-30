"""OCI provider configuration."""

import os
from typing import Any, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from orb.infrastructure.interfaces.provider import BaseProviderConfig
from orb.providers.oci.configuration.realms import (
    REALM_DOMAIN_PATTERN,
    is_allowed_realm_domain,
    is_valid_extra_realm_suffix,
)


class OCIProviderConfig(BaseSettings, BaseProviderConfig):  # type: ignore[misc]
    """Base OCI provider configuration for strategy construction."""

    model_config = SettingsConfigDict(  # type: ignore[assignment]
        env_prefix="ORB_OCI_",
        case_sensitive=False,
        populate_by_name=True,
        env_nested_delimiter="__",
        extra="allow",
    )

    provider_type: str = "oci"
    region: str = Field(default="us-phoenix-1", description="OCI region")  # type: ignore[assignment]
    realm_domain_component: str = Field(
        default="oraclecloud.com",
        pattern=REALM_DOMAIN_PATTERN.pattern,
        description="OCI realm DNS suffix for signed Compute launch requests",
    )
    extra_realm_suffixes: list[str] = Field(
        default_factory=list,
        description=(
            "Additional realm DNS suffixes (exact domains) allowed for realm_domain_component "
            "beyond the realms published in the OCI SDKs"
        ),
    )
    credential_source: Optional[str] = Field(
        default=None,
        description=(
            "OCI auth mode for CLI calls: instance_principal, resource_principal, "
            "api_key, security_token, profile, or default"
        ),
    )
    profile: Optional[str] = Field(default=None, description="OCI config profile")
    tenancy_ocid: Optional[str] = Field(default=None, description="OCI tenancy OCID")
    user_ocid: Optional[str] = Field(default=None, description="OCI user OCID")
    fingerprint: Optional[str] = Field(default=None, description="OCI API key fingerprint")
    private_key_path: Optional[str] = Field(
        default=None, description="OCI API key private key path"
    )

    @field_validator("extra_realm_suffixes")
    @classmethod
    def validate_extra_realm_suffixes(cls, value: list[str]) -> list[str]:
        """Each extra suffix must be a lowercase domain with an alphabetic top-level label."""
        if not all(is_valid_extra_realm_suffix(suffix) for suffix in value):
            raise ValueError(
                "extra_realm_suffixes entries must be lowercase domains with at least "
                "two labels and an alphabetic top-level label"
            )
        return value

    @model_validator(mode="after")
    def validate_realm_domain(self) -> "OCIProviderConfig":
        """Reject realm suffixes that are neither known nor explicitly allowed."""
        if not is_allowed_realm_domain(self.realm_domain_component, self.extra_realm_suffixes):
            raise ValueError(
                "realm_domain_component is not a known OCI realm; "
                "add it to extra_realm_suffixes to allow it"
            )
        return self

    @field_validator("private_key_path", mode="before")
    @classmethod
    def normalize_private_key_path(cls, value: Any) -> str | None:
        """Resolve user/environment shorthand consistently before invoking OCI CLI."""
        if value is None or not str(value).strip():
            return None
        return os.path.abspath(os.path.expandvars(os.path.expanduser(str(value).strip())))

    def validate_auth_configuration(
        self, credential_source: Optional[str] = None, **overrides: Any
    ) -> tuple[bool, str, list[str], str]:
        """Validate auth configuration without making network calls.

        Returns:
            (is_valid, message, missing_fields, resolved_source)
        """
        effective = {
            "profile": overrides.get("profile", self.profile),
            "tenancy_ocid": overrides.get("tenancy_ocid", self.tenancy_ocid),
            "user_ocid": overrides.get("user_ocid", self.user_ocid),
            "fingerprint": overrides.get("fingerprint", self.fingerprint),
            "private_key_path": overrides.get("private_key_path", self.private_key_path),
        }

        source = (credential_source or self.credential_source or "default").lower()
        api_key_fields = ["tenancy_ocid", "user_ocid", "fingerprint", "private_key_path"]

        if source in {"instance_principal", "resource_principal"}:
            return True, "Principal-based OCI auth selected", [], source

        if source == "security_token":
            if not effective.get("profile"):
                return False, "OCI session authentication requires a profile", ["profile"], source
            return True, "OCI session profile configuration is valid", [], source

        if source == "api_key":
            missing = [field for field in api_key_fields if not effective.get(field)]
            if missing:
                return False, "Missing required OCI API key fields", missing, source
            if not os.path.isfile(
                self.normalize_private_key_path(effective["private_key_path"]) or ""
            ):
                return (
                    False,
                    "OCI API private key must be an existing file",
                    ["private_key_path"],
                    source,
                )
            return True, "OCI API key configuration is valid", [], source

        if source not in {"default", "profile"}:
            return False, f"Unsupported credential source: {credential_source}", [], source

        if effective.get("profile"):
            return True, "OCI profile configuration is valid", [], "profile"

        if source == "default":
            return True, "Default OCI CLI credentials selected", [], "default"

        missing = [field for field in api_key_fields if not effective.get(field)]
        if missing:
            return (
                False,
                "No usable OCI auth configuration found (profile or complete api_key fields required)",
                missing,
                "default",
            )
        if not os.path.isfile(self.normalize_private_key_path(effective["private_key_path"]) or ""):
            return (
                False,
                "OCI API private key must be an existing file",
                ["private_key_path"],
                "api_key",
            )
        return True, "OCI API key configuration is valid", [], "api_key"
