"""Normalisation of raw provider configuration input.

Registry config factories receive either a plain mapping or a whole
``ProviderInstanceConfig``.  The ``type`` / ``provider_type`` keys select which
provider to build; they are not fields of any provider config model.
"""

from __future__ import annotations

from collections.abc import Mapping
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
