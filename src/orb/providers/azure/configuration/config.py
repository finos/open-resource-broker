"""Azure configuration provider - single source of truth."""

import re
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from orb.infrastructure.interfaces.provider import BaseProviderConfig

class CycleCloudConfig(BaseModel):
    """CycleCloud connection configuration."""

    model_config = ConfigDict(extra="allow")

    url: Optional[str] = Field(None, description="CycleCloud REST API base URL")
    credential_path: Optional[str] = Field(
        None,
        description="Path to a JSON file containing CycleCloud credentials and optional auth overrides",
    )
    verify_ssl: Optional[bool] = Field(
        None,
        description="Whether to verify TLS certificates for CycleCloud API calls.",
    )
    auth_mode: Optional[str] = Field(
        None,
        description="CycleCloud auth mode override, e.g. 'basic' or 'bearer'.",
    )
    aad_scope: Optional[str] = Field(
        None,
        description="AAD scope used to resolve a bearer token for CycleCloud.",
    )

    @model_validator(mode="before")
    @classmethod
    def reject_inline_basic_auth(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data

        forbidden_fields = {
            "username",
            "password",
            "bearer_token",
            "cyclecloud_username",
            "cyclecloud_password",
            "cyclecloud_bearer_token",
        }
        present = [field for field in forbidden_fields if data.get(field) not in (None, "")]
        if present:
            raise ValueError(
                "CycleCloud inline username/password config is not supported; "
                "use credential_path instead."
            )
        return data


# ---------------------------------------------------------------------------
# Root provider config
# ---------------------------------------------------------------------------

class AzureProviderConfig(BaseProviderConfig):
    """Configuration for the Azure provider (VMSS / Compute Fleet)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # ------------------------------------------------------------------
    # Provider identity
    # ------------------------------------------------------------------
    provider_type: str = Field("azure", description="Provider type identifier")
    region: str = Field("eastus2", description="Azure region / location slug")

    # ------------------------------------------------------------------
    # Azure subscription & resource targeting
    # ------------------------------------------------------------------
    subscription_id: Optional[str] = Field(
        None, description="Azure subscription ID (UUID)"
    )
    resource_group: Optional[str] = Field(
        None,
        description="Default resource group for created resources (1-90 chars)",
    )

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    client_id: Optional[str] = Field(
        None,
        description="Managed-identity client ID for user-assigned identity selection",
    )

    # ------------------------------------------------------------------
    # Retry / timeout
    # ------------------------------------------------------------------
    max_retries: int = Field(
        3, ge=0, description="Maximum SDK retry attempts for transient errors"
    )
    connect_timeout: int = Field(
        30, ge=1, description="Connection timeout for ARM API calls in seconds"
    )
    read_timeout: int = Field(
        60, ge=1, description="Read timeout for ARM API calls in seconds"
    )

    # ------------------------------------------------------------------
    # CycleCloud
    # ------------------------------------------------------------------
    cyclecloud: Optional[CycleCloudConfig] = Field(
        default=None,
        description="CycleCloud integration configuration (URL, credentials, TLS verification)",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("resource_group")
    @classmethod
    def validate_resource_group(cls, v: Optional[str]) -> Optional[str]:
        """Enforce ARM resource-group naming rules."""
        if v is None:
            return v
        if not (1 <= len(v) <= 90):
            raise ValueError("resource_group must be 1-90 characters")
        if not re.match(r"^[a-zA-Z0-9_\-.()\[\]]+$", v):
            raise ValueError(
                "resource_group contains invalid characters "
                "(allowed: alphanumeric, _, -, ., (, ), [, ])"
            )
        return v

    @field_validator("subscription_id")
    @classmethod
    def validate_subscription_id(cls, v: Optional[str]) -> Optional[str]:
        """Validate UUID-like subscription ID format."""
        if v is None:
            return v
        if not re.match(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            v,
        ):
            raise ValueError(
                "subscription_id must be a valid UUID "
                "(xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)"
            )
        return v
