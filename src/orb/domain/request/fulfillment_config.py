"""Resolution of the fulfilment settings that apply to a request.

Provisioning and the fulfilment state machine must agree on how long a request
may spend being created, so both resolve the settings through this one module:
the configured defaults, overlaid by the request's own ``fulfillment_config``
metadata.

Pure domain: no infrastructure imports.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def fulfillment_defaults_from_request_config(request_config: Mapping[str, Any]) -> dict[str, Any]:
    """Build the default fulfilment settings from the ``request`` configuration section."""
    return {
        "max_retries": request_config.get("fulfillment_max_retries", 3),
        "timeout_seconds": request_config.get("fulfillment_timeout_seconds", 300),
        "dispatch_timeout_seconds": request_config.get("dispatch_timeout_seconds", 300),
        "batch_size": request_config.get("fulfillment_batch_size", 1000),
        "fallback_template_id": request_config.get("fulfillment_fallback_template_id"),
    }


def merged_fulfillment_config(defaults: Mapping[str, Any], request: Any) -> dict[str, Any]:
    """Overlay the request's ``fulfillment_config`` metadata on ``defaults``.

    The merge is shallow: any key the request sets replaces the default.
    """
    return {**defaults, **request.metadata.get("fulfillment_config", {})}
