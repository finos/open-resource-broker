"""GCP provider configuration."""

from __future__ import annotations

import re
from typing import Optional

from pydantic import ConfigDict, Field, field_validator, model_validator

from orb.infrastructure.interfaces.provider import BaseProviderConfig
_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_REGION_RE = re.compile(r"^[a-z]+-[a-z0-9]+[0-9]$")
_ZONE_RE = re.compile(r"^[a-z]+-[a-z0-9]+[0-9]-[a-z]$")


class GCPProviderConfig(BaseProviderConfig):
    """Configuration for the GCP provider."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    provider_type: str = Field("gcp", description="Provider type identifier")
    project_id: str = Field(..., description="GCP project ID used for Compute Engine operations")
    region: str = Field("us-central1", description="Default GCP region")
    zones: list[str] = Field(default_factory=list, description="Optional preferred zones")
    network: Optional[str] = Field(None, description="Default VPC network self-link or name")
    subnetwork: Optional[str] = Field(
        None, description="Default subnetwork self-link or name"
    )
    max_retries: int = Field(3, ge=0, description="Maximum retry attempts for GCP API calls")
    connect_timeout: int = Field(30, ge=1, description="Connection timeout in seconds")
    read_timeout: int = Field(60, ge=1, description="Read timeout in seconds")
    use_application_default_credentials: bool = Field(
        True,
        description="Use Application Default Credentials. GCP provider supports ADC only.",
    )
    credential_file: Optional[str] = Field(
        None,
        description="Optional service-account key file path reference used by ADC-compatible tooling.",
    )

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        """Validate a canonical GCP project ID."""
        if not _PROJECT_RE.match(value):
            raise ValueError(
                "project_id must match the canonical GCP project ID format"
            )
        return value

    @field_validator("region")
    @classmethod
    def validate_region(cls, value: str) -> str:
        """Validate GCP region format."""
        if not _REGION_RE.match(value):
            raise ValueError("region must look like 'us-central1' or 'europe-west4'")
        return value

    @field_validator("zones")
    @classmethod
    def validate_zones(cls, value: list[str]) -> list[str]:
        """Validate GCP zones."""
        for zone in value:
            if not _ZONE_RE.match(zone):
                raise ValueError(
                    "zones must contain zone slugs like 'us-central1-a' or 'europe-west4-b'"
                )
        return value

    @model_validator(mode="after")
    def validate_auth_mode(self) -> GCPProviderConfig:
        """Enforce ADC-only auth semantics."""
        # GCP auth is intentionally limited to Application Default Credentials:
        # https://cloud.google.com/docs/authentication/application-default-credentials
        if not self.use_application_default_credentials:
            raise ValueError("GCP provider supports ADC only")
        return self
