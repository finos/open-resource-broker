"""Request models for API handlers."""

import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def to_camel(snake_str: str) -> str:
    """Convert snake_case to camelCase for API boundary."""
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class BaseRequestModel(BaseModel):
    """
    Base class for API request models with camelCase support.

    These models handle external API requests that use camelCase format.
    This is appropriate at the API boundary layer.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,  # Allow populating by field name (snake_case)
    )


class MachineReferenceModel(BaseRequestModel):
    """Model for machine reference in requests."""

    name: str
    machine_id: Optional[str] = None


class RequestMachinesModel(BaseRequestModel):
    """Model for request machines API."""

    template: dict[str, Any]

    @property
    def template_id(self) -> str:
        """Get template ID from template dictionary."""
        return self.template.get("templateId", "")

    @property
    def machine_count(self) -> int:
        """Get machine count from template dictionary."""
        return int(self.template.get("machineCount", 0))


class RequestStatusModel(BaseRequestModel):
    """Model for request status API."""

    requests: list[dict[str, Any]]

    @field_validator("requests")
    @classmethod
    def validate_request_ids(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Validate that all request IDs have proper req-/ret- prefix."""
        for request_dict in v:
            request_id = request_dict.get("requestId", "")
            if request_id and not re.match(r"^(req-|ret-)[a-f0-9\-]{36}$", request_id):
                raise ValueError(
                    f"Invalid request ID format: '{request_id}'. "
                    "Request IDs must start with 'req-' or 'ret-' followed by a UUID."
                )
        return v

    @property
    def request_ids(self) -> list[str]:
        """Get request IDs from requests list."""
        return [r.get("requestId", "") for r in self.requests if "requestId" in r]


class RequestReturnMachinesModel(BaseRequestModel):
    """Model for request return machines API."""

    machines: list[dict[str, Any]]

    @property
    def machine_names(self) -> list[str]:
        """Get machine names from machines list."""
        return [m.get("name", "") for m in self.machines if "name" in m]

    @property
    def machine_ids(self) -> list[str]:
        """Get machine IDs from machines list."""
        return [m.get("machineId", "") for m in self.machines if "machineId" in m]


class GetReturnRequestsModel(BaseRequestModel):
    """Model for get return requests API."""

    machines: Optional[list[dict[str, Any]]] = Field(default_factory=list)

    @property
    def machine_names(self) -> list[str]:
        """Get machine names from machines list."""
        if not self.machines:
            return []
        return [m.get("name", "") for m in self.machines if "name" in m]
