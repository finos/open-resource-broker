"""Region/location default a template row takes from its provider instance.

Providers such as Azure (``location``) and GCP (``region``) require a location
on the template. The runtime fills it from the instance configuration; this
module lets default resolution do the same, so validation and loading agree.
"""

from typing import Any, Iterable, Optional

# camelCase and snake_case spell these one-word fields identically.
_REGION_KEYS = ("region", "location")


def _carries_region(source: Any) -> bool:
    if source is None:
        return False
    if not isinstance(source, dict):
        dump = getattr(source, "model_dump", None)
        source = dump() if callable(dump) else {}
    return any(source.get(key) not in (None, "") for key in _REGION_KEYS)


def has_region_value(resolved: dict[str, Any]) -> bool:
    """True when region or location holds a non-None, non-empty value.

    The Azure and GCP templates read a nested ``provider_config`` region or
    location when the top-level one is empty, so a nested value counts too.
    """
    return _carries_region(resolved) or _carries_region(resolved.get("provider_config"))


def sole_enabled_instance(providers: Optional[Iterable[Any]], provider_type: str) -> Optional[Any]:
    """The only enabled instance of *provider_type*, else None."""
    matches = [p for p in providers or [] if p.type == provider_type and p.enabled]
    return matches[0] if len(matches) == 1 else None


def region_seed(
    extension_class: Optional[type], instance_config: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Instance region/location keyed by the field(s) the provider's extension declares.

    Providers whose extension declares neither field (AWS, k8s, OCI) get nothing.
    """
    fields = getattr(extension_class, "model_fields", None) or {}
    config = instance_config or {}
    value = config.get("region") or config.get("location")
    if not value:
        return {}
    return {key: value for key in _REGION_KEYS if key in fields}
