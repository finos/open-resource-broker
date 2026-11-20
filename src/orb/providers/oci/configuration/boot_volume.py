"""Validation for OCI boot-volume performance, independent of boot size."""

from typing import Annotated, Any

from pydantic import AfterValidator, StrictInt, TypeAdapter


def _supported_vpus(value: int) -> int:
    # InstanceSourceViaImageDetails documents 10, 20, and the range 30-120.
    if value not in (10, 20) and not 30 <= value <= 120:
        raise ValueError("boot_volume_vpus_per_gb must be 10, 20, or an integer from 30 to 120")
    return value


BootVolumeVPUsPerGB = Annotated[StrictInt, AfterValidator(_supported_vpus)]
_VPUS_ADAPTER = TypeAdapter(BootVolumeVPUsPerGB | None)


def validate_boot_volume_vpus_per_gb(value: Any) -> int | None:
    """Apply the same strict numeric contract in typed and raw-template paths."""
    return _VPUS_ADAPTER.validate_python(value)


def select_boot_volume_performance(data: dict[str, Any]) -> Any:
    """Match the mapper's authored source/key precedence for only this option."""
    provider_config = data.get("provider_config")
    configuration = data.get("configuration")
    metadata = data.get("metadata")
    combined = {
        **(provider_config if isinstance(provider_config, dict) else {}),
        **(configuration if isinstance(configuration, dict) else {}),
    }
    sources = (data, combined, metadata if isinstance(metadata, dict) else {})
    for source in sources:
        for name in ("boot_volume_vpus_per_gb", "bootVolumeVpusPerGB"):
            if source.get(name) is not None:
                return source[name]
    return None


def normalize_boot_volume_performance_alias(data: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize only this option, preserving existing OCI field precedence."""
    result = dict(data)
    value = select_boot_volume_performance(data)
    if value is not None:
        result["boot_volume_vpus_per_gb"] = value
    return result


def preserve_boot_volume_performance_override(
    *, row: dict[str, Any], defaults: dict[str, Any], merged: dict[str, Any]
) -> tuple[dict[str, Any], None]:
    """Keep an explicitly saved OCI option above lower flat provider defaults.

    Provider DTOs save this option in provider_config. The existing row hook
    retains the authored choice after the shared defaults merge, without
    changing precedence or migration rules for any other field.
    """
    value = select_boot_volume_performance(row)
    if value is not None:
        result = dict(merged)
        result.pop("bootVolumeVpusPerGB", None)
        result["boot_volume_vpus_per_gb"] = value
        return result, None
    return merged, None
