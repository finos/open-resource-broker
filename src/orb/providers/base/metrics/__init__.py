"""Provider metrics abstractions.

This package contains the ProviderMetricsPort interface and its default
no-op implementation.  The OTel-backed implementation lives here too.

Provider metric backends (AWS BotocoreMetricsHandler, k8s K8sMetrics) are
owned by their respective provider packages and registered via DI.  This
package only defines the shared port and the app-wide OTel implementation
used for non-provider emit sites.
"""

from orb.providers.base.metrics.provider_metrics_port import (
    NoOpProviderMetrics,
    OtelProviderMetrics,
    ProviderMetricsPort,
)

__all__ = [
    "NoOpProviderMetrics",
    "OtelProviderMetrics",
    "ProviderMetricsPort",
]
