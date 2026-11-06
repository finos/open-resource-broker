"""Normalisation of raw provider configuration input.

Registry config factories receive either a plain mapping or a whole
``ProviderInstanceConfig``.  The ``type`` / ``provider_type`` keys select which
provider to build; they are not fields of any provider config model.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import threading
from collections.abc import Iterable, Mapping
from typing import Any

DISCRIMINATOR_KEYS = ("type", "provider_type")


def provider_config_mapping(data: Any) -> Any:
    """Return the provider's own settings, as a new dict for mappings.

    Unwraps a ``ProviderInstanceConfig`` (its ``config``), accepts any mapping,
    and drops the provider-type discriminator keys.  ``None`` yields an empty
    dict.  The input is never mutated.  A value that is not a mapping (for
    example a typed settings model) is returned unchanged.
    """
    if data is None:
        return {}
    if not isinstance(data, Mapping) and hasattr(data, "config"):
        data = data.config
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        # A typed settings model has no mapping form; keys are only stripped from mappings.
        return data  # type: ignore[no-any-return]
    return {k: v for k, v in data.items() if k not in DISCRIMINATOR_KEYS}


_warned: set[tuple[str, str]] = set()
_warned_lock = threading.Lock()


def provider_instance_name(data: Any) -> str | None:
    """Return the instance name of a ``ProviderInstanceConfig`` or a mapping with a ``name``."""
    from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig

    if isinstance(data, ProviderInstanceConfig):
        return data.name
    if isinstance(data, Mapping):
        name = data.get("name")
        return name if isinstance(name, str) else None
    return None


def _known_config_keys(config_cls: Any, consumed_keys: Iterable[str]) -> set[str]:
    """Field names, aliases and consumed legacy keys accepted by ``config_cls``."""
    known: set[str] = set(DISCRIMINATOR_KEYS) | set(consumed_keys)
    for name, info in config_cls.model_fields.items():
        known.add(name)
        for alias in (info.alias, info.validation_alias):
            if isinstance(alias, str):
                known.add(alias)
            elif alias is not None:
                known.update(c for c in getattr(alias, "choices", ()) if isinstance(c, str))
    return known


def _instance_key(raw: Mapping[str, Any], instance_key: str | None) -> str:
    if instance_key:
        return instance_key
    digest = hashlib.sha256(
        json.dumps(sorted(raw), default=str).encode("utf-8"), usedforsecurity=False
    )
    return f"config-{digest.hexdigest()[:12]}"


def warn_unknown_config_keys(
    config_cls: Any,
    raw: Mapping[str, Any],
    instance_key: str | None,
    logger: Any,
    consumed_keys: Iterable[str] = (),
) -> list[str]:
    """Warn once per provider instance about keys ``config_cls`` does not define.

    Provider configs that allow extra fields accept a misspelled key without
    complaint and silently fall back to the default.  Call this only where a
    config is built from user input, not where an existing config is rebuilt,
    so each unknown key is reported once instead of on every construction.

    ``consumed_keys`` lists legacy keys the config handles itself.  Only key
    names are logged, never values.  Returns the unknown keys found, whether or
    not they were already reported.
    """
    known = _known_config_keys(config_cls, consumed_keys)
    unknown = [str(key) for key in raw if key not in known]
    if not unknown:
        return []

    owner = _instance_key(raw, instance_key)
    for key in unknown:
        with _warned_lock:
            if (owner, key) in _warned:
                continue
            _warned.add((owner, key))
        close = difflib.get_close_matches(key, sorted(known), n=1)
        hint = f" Did you mean '{close[0]}'?" if close else ""
        logger.warning("Unknown key '%s' in provider config '%s' is ignored.%s", key, owner, hint)
    return unknown
