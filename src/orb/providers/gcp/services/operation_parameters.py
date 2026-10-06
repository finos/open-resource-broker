"""Validated GCP provider operation parameter contracts."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from orb.domain.request.aggregate import Request
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


class GCPMachineCoordinates(BaseModel):
    """Persisted provider coordinates supplied by machine commands."""

    model_config = ConfigDict(extra="ignore")

    provider_api: GCPProviderApi
    resource_id: NonEmptyString | None = None
    provider_data: GCPRequestMetadataParameters


class GCPMutationParameters(BaseModel):
    """Validated GCP mutation/read operation parameters."""

    model_config = ConfigDict(extra="ignore")

    instance_ids: list[NonEmptyString] = Field(default_factory=list)
    resource_ids: list[NonEmptyString] = Field(default_factory=list)
    resource_id: NonEmptyString | None = None
    resource_mapping: dict[NonEmptyString, tuple[NonEmptyString, int]] = Field(default_factory=dict)
    provider_api: GCPProviderApi | None = None
    region: NonEmptyString | None = None
    zone: NonEmptyString | None = None
    zones: list[NonEmptyString] = Field(default_factory=list)
    requested_count: int | None = Field(default=None, ge=0)
    machine_coordinates: dict[NonEmptyString, GCPMachineCoordinates] = Field(default_factory=dict)
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
        data = dict(operation.parameters)
        request = data.pop("request", None)
        if isinstance(request, Request):
            if request.provider_api:
                data.setdefault("provider_api", request.provider_api)
            data.setdefault("request_metadata", request.provider_data)
        try:
            params = cls.model_validate(data)
        except ValidationError as exc:
            raise GCPValidationError("Invalid GCP mutation operation parameters") from exc
        params.fill_metadata_from_machine_coordinates()
        return params

    def _targeted_coordinates(self) -> list[GCPMachineCoordinates]:
        """Return the persisted coordinates of the machines this operation targets."""
        if not self.instance_ids:
            return list(self.machine_coordinates.values())
        return [
            self.machine_coordinates[instance_id]
            for instance_id in self.instance_ids
            if instance_id in self.machine_coordinates
        ]

    def fill_metadata_from_machine_coordinates(self) -> None:
        """Fill placement the request lacks from the targeted machines' persisted data.

        Return requests carry no placement of their own; the machines (and the
        request that provisioned them) do. Values already present on the
        request metadata win. A scope is only taken when every targeted machine
        agrees on it, so a mixed set never resolves to a guessed value.
        """
        coordinates = self._targeted_coordinates()
        if not coordinates:
            return
        metadata = self.request_metadata

        scopes = {c.provider_data.scope for c in coordinates if c.provider_data.scope}
        if metadata.scope is None and scopes:
            if len(scopes) > 1:
                raise GCPValidationError(
                    "Targeted machines disagree on MIG scope", details={"scopes": sorted(scopes)}
                )
            metadata.scope = scopes.pop()

        location_field = {"zonal": "zone", "regional": "region"}.get(metadata.scope or "")
        if location_field is not None and getattr(metadata, location_field) is None:
            locations = {
                getattr(c.provider_data, location_field)
                for c in coordinates
                if getattr(c.provider_data, location_field)
            }
            if len(locations) == 1:
                setattr(metadata, location_field, locations.pop())

        if metadata.project_id is None:
            projects = {
                c.provider_data.project_id for c in coordinates if c.provider_data.project_id
            }
            if len(projects) == 1:
                metadata.project_id = projects.pop()

    def coordinate_resource_ids(self) -> list[str]:
        """Return the distinct resource ids of the targeted machines, in order."""
        resource_ids: list[str] = []
        for coordinate in self._targeted_coordinates():
            if coordinate.resource_id and coordinate.resource_id not in resource_ids:
                resource_ids.append(coordinate.resource_id)
        return resource_ids
