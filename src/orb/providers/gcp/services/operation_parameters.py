"""Validated GCP provider operation parameter contracts."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from orb.providers.base.strategy import ProviderOperation
from orb.providers.gcp.domain.template.value_objects import GCPProviderApi
from orb.providers.gcp.exceptions import GCPValidationError

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class GCPRequestMetadataParameters(BaseModel):
    """GCP request metadata fields used to resume provider operations."""

    model_config = ConfigDict(extra="ignore")

    project_id: NonEmptyString | None = None
    region: NonEmptyString | None = None
    zone: NonEmptyString | None = None
    scope: NonEmptyString | None = None
    mig_name: NonEmptyString | None = None
    instance_template_name: NonEmptyString | None = None
    provider_api: GCPProviderApi | None = None


class GCPMutationParameters(BaseModel):
    """Validated GCP mutation/read operation parameters."""

    model_config = ConfigDict(extra="ignore")

    instance_ids: list[NonEmptyString] = Field(default_factory=list)
    resource_ids: list[NonEmptyString] = Field(default_factory=list)
    resource_id: NonEmptyString | None = None
    resource_mapping: dict[NonEmptyString, tuple[NonEmptyString, int]] = Field(
        default_factory=dict
    )
    provider_api: GCPProviderApi | None = None
    region: NonEmptyString | None = None
    zone: NonEmptyString | None = None
    zones: list[NonEmptyString] = Field(default_factory=list)
    request_metadata: GCPRequestMetadataParameters = Field(
        default_factory=GCPRequestMetadataParameters
    )

    @property
    def provider_api_name(self) -> str | None:
        """Return the operation's explicit provider API, including request metadata."""
        provider_api = self.provider_api or self.request_metadata.provider_api
        if provider_api is None:
            return None
        return provider_api.value

    @classmethod
    def from_operation(cls, operation: ProviderOperation) -> Self:
        """Validate raw provider operation parameters at the GCP boundary."""
        try:
            return cls.model_validate(operation.parameters)
        except ValidationError as exc:
            raise GCPValidationError("Invalid GCP mutation operation parameters") from exc
