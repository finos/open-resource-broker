"""Kubernetes provider implementation.

All direct ``kubernetes`` SDK imports are confined to this subtree (enforced by
the architecture test in ``tests/unit/architecture/test_k8s_leak_detection.py``).
"""

from typing import TYPE_CHECKING, Any

from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.configuration.template_extension import (
    K8sTemplateExtensionConfig,
)
from orb.providers.k8s.registration import (
    get_k8s_extension_defaults,
    initialize_k8s_provider,
    is_k8s_provider_registered,
    register_k8s_provider,
)

if TYPE_CHECKING:
    from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy


def __getattr__(name: str) -> Any:
    # The strategy pulls in the cloud SDK, so it is imported on first access
    # rather than whenever the provider package is imported.
    if name == "K8sProviderStrategy":
        from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

        return K8sProviderStrategy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__: list[str] = [
    "K8sProviderConfig",
    "K8sProviderStrategy",
    "K8sTemplateExtensionConfig",
    "get_k8s_extension_defaults",
    "initialize_k8s_provider",
    "is_k8s_provider_registered",
    "register_k8s_provider",
]
