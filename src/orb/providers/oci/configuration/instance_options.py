"""OCI instance metadata endpoint options."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from orb.providers.oci.configuration.boot_volume import preserve_boot_volume_performance_override


class OCIInstanceOptions(BaseModel):
    """Optional launch settings; omission preserves OCI's metadata defaults."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    are_legacy_imds_endpoints_disabled: StrictBool | None = Field(
        default=None, alias="areLegacyImdsEndpointsDisabled"
    )


def _authored_launch_option(row: dict[str, Any], *names: str) -> Any:
    """Use the mapper's source and alias precedence before defaults are merged."""
    provider_config = row.get("provider_config")
    configuration = row.get("configuration")
    metadata = row.get("metadata")
    combined = {
        **(provider_config if isinstance(provider_config, dict) else {}),
        **(configuration if isinstance(configuration, dict) else {}),
    }
    for source in (row, combined, metadata if isinstance(metadata, dict) else {}):
        for name in names:
            if source.get(name) is not None:
                return source[name]
    return None


def preserve_launch_controls_overrides(
    *, row: dict[str, Any], defaults: dict[str, Any], merged: dict[str, Any]
) -> tuple[dict[str, Any], None]:
    """Keep saved launch choices above defaults without changing unset semantics."""
    merged, _ = preserve_boot_volume_performance_override(row=row, defaults=defaults, merged=merged)
    capacity_type = _authored_launch_option(row, "capacity_type", "capacityType")
    if capacity_type is not None:
        if isinstance(capacity_type, (str, list, dict, set, tuple)) and not capacity_type:
            capacity_type = _authored_launch_option(defaults, "capacity_type", "capacityType")
            if capacity_type is None:
                capacity_type = "ondemand"
        # Keep invalid authored values too, so normal template validation rejects
        # them rather than silently replacing them with a valid lower default.
        merged = {**merged, "capacity_type": capacity_type}
    raw_options = _authored_launch_option(row, "instance_options", "instanceOptions")
    if raw_options is not None:
        options = OCIInstanceOptions.model_validate(raw_options)
        if options.are_legacy_imds_endpoints_disabled is not None:
            merged = {
                **merged,
                "instance_options": options.model_dump(by_alias=True, exclude_none=True),
            }
        # DTOs serialize both {} and a null option as an unset value.
    return merged, None
