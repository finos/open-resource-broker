"""GCP-specific template value objects."""

from __future__ import annotations

import re
from enum import Enum
from typing import ClassVar

from pydantic import field_validator, model_serializer, model_validator

from orb.domain.base.value_objects import ValueObject

_REGION_RE = re.compile(r"^[a-z]+-[a-z0-9]+[0-9]$")
_ZONE_RE = re.compile(r"^[a-z]+-[a-z0-9]+[0-9]-[a-z]$")
_RESOURCE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")

# https://cloud.google.com/compute/docs/naming-resources: Compute Engine
# resource names (instances, instance templates, managed instance groups)
# must satisfy RFC1035 and be at most 63 characters.
RFC1035_LABEL_PATTERN = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
GCP_RESOURCE_NAME_MAX_LENGTH = 63


def validate_rfc1035_label(value: str, *, field_name: str, max_length: int = 63) -> str:
    """Validate a GCP resource-name label against RFC1035.

    ``max_length`` lets callers that build a longer resource name by
    prefixing/suffixing ``value`` (for example
    ``f"orb-mig-{value}-{uuid}"``) pass a smaller budget so the final
    generated name still fits GCP's 63-character ceiling.
    """
    if not value:
        raise ValueError(f"{field_name} cannot be empty")
    if len(value) > max_length:
        raise ValueError(f"{field_name} must be at most {max_length} characters, got {value!r}")
    if not RFC1035_LABEL_PATTERN.match(value):
        raise ValueError(
            f"{field_name} must match RFC1035 ([a-z]([-a-z0-9]*[a-z0-9])?): "
            f"lowercase letters, digits, and hyphens, starting with a letter, got {value!r}"
        )
    return value


class GCPProviderApi(str, Enum):
    """GCP provider APIs."""

    MIG = "MIG"
    SINGLE_VM = "SingleVM"


class _GCPStringValue(ValueObject):
    """Scalar GCP string value object."""

    value: str
    _pattern: ClassVar[re.Pattern[str]]
    _error: ClassVar[str]

    @model_validator(mode="before")
    @classmethod
    def coerce_string(cls, data: object) -> object:
        """Accept raw string input for value-object validation."""
        if isinstance(data, str):
            return {"value": data}
        return data

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: str) -> str:
        """Normalize and validate the wrapped string value."""
        value = value.strip()
        if not value:
            raise ValueError(f"{cls.__name__} cannot be empty")
        if not cls._pattern.match(value):
            raise ValueError(cls._error)
        return value

    @model_serializer
    def serialize_model(self) -> str:
        """Serialize the value object as its scalar string."""
        return self.value

    def __str__(self) -> str:
        return self.value


class GCPRegion(_GCPStringValue):
    """GCP region slug."""

    _pattern = _REGION_RE
    _error = "region must look like 'us-central1' or 'europe-west4'"


class GCPZone(_GCPStringValue):
    """GCP zone slug."""

    _pattern = _ZONE_RE
    _error = "zone must look like 'us-central1-a' or 'europe-west4-b'"


class GCPProjectId(_GCPStringValue):
    """GCP project ID."""

    _pattern = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
    _error = "project_id must match the canonical GCP project ID format"


class GCPDiskTypeName(_GCPStringValue):
    """Compute Engine disk type resource name."""

    _pattern = _RESOURCE_NAME_RE
    _error = "boot_disk_type must be a disk type resource name such as 'pd-balanced'"


class GCPMIGScope(str, Enum):
    """Managed Instance Group scope."""

    REGIONAL = "regional"
    ZONAL = "zonal"
