"""Backward-compatibility shim — re-exports from instrumentation.metrics.

All symbols have been moved to
:mod:`orb.providers.k8s.infrastructure.instrumentation.metrics`.
Import from that module directly for new code; this shim exists only
so existing importers do not break before they are updated.
"""

from orb.providers.k8s.infrastructure.instrumentation.metrics import (
    _METRIC_SPECS,
    API_ERROR_CODES,
    API_OPERATIONS,
    POD_CREATION_STATUSES,
    WATCH_EVENT_TYPES,
    WATCH_RECONNECT_REASONS,
    K8sMetrics,
    _NoOpCounter,
    _NoOpHistogram,
    _NoOpMeter,
    _NoOpUpDownCounter,
    _validate_label,
)

__all__ = [
    "API_ERROR_CODES",
    "API_OPERATIONS",
    "POD_CREATION_STATUSES",
    "WATCH_EVENT_TYPES",
    "WATCH_RECONNECT_REASONS",
    "K8sMetrics",
    "_METRIC_SPECS",
    "_NoOpCounter",
    "_NoOpHistogram",
    "_NoOpMeter",
    "_NoOpUpDownCounter",
    "_validate_label",
]
