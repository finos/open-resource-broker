"""OCI provider configuration."""

from typing import Any, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from orb.infrastructure.interfaces.provider import BaseProviderConfig


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
    region: str = Field("us-phoenix-1", description="OCI region")  # type: ignore[assignment]
    profile: Optional[str] = Field(None, description="OCI config profile")
    tenancy_ocid: Optional[str] = Field(None, description="OCI tenancy OCID")
    user_ocid: Optional[str] = Field(None, description="OCI user OCID")
    fingerprint: Optional[str] = Field(None, description="OCI API key fingerprint")
    private_key_path: Optional[str] = Field(None, description="OCI API key private key path")

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

        source = (credential_source or "default").lower()
        api_key_fields = ["tenancy_ocid", "user_ocid", "fingerprint", "private_key_path"]

        if source in {"instance_principal", "resource_principal"}:
            return True, "Principal-based OCI auth selected", [], source

        if source == "api_key":
            missing = [field for field in api_key_fields if not effective.get(field)]
            if missing:
                return False, "Missing required OCI API key fields", missing, source
            return True, "OCI API key configuration is valid", [], source

        if source not in {"default", "profile"}:
            return False, f"Unsupported credential source: {credential_source}", [], source

        if effective.get("profile"):
            return True, "OCI profile configuration is valid", [], "profile"

        missing = [field for field in api_key_fields if not effective.get(field)]
        if missing:
            return (
                False,
                "No usable OCI auth configuration found (profile or complete api_key fields required)",
                missing,
                "default",
            )
        return True, "OCI API key configuration is valid", [], "api_key"
