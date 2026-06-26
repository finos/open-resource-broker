"""Kubernetes provider implementation.

Mirrors the AWS provider shape under ``src/orb/providers/aws/``.  All direct
``kubernetes`` SDK imports are confined to this subtree (enforced by the
architecture test in ``tests/unit/architecture/test_kubernetes_leak_detection.py``).
"""

from orb.providers.kubernetes.configuration.config import KubernetesProviderConfig
from orb.providers.kubernetes.configuration.template_extension import (
    KubernetesTemplateExtensionConfig,
)
from orb.providers.kubernetes.registration import (
    get_kubernetes_extension_defaults,
    initialize_kubernetes_provider,
    is_kubernetes_provider_registered,
    register_kubernetes_provider,
)
from orb.providers.kubernetes.strategy.kubernetes_provider_strategy import (
    KubernetesProviderStrategy,
)

__all__: list[str] = [
    "KubernetesProviderConfig",
    "KubernetesProviderStrategy",
    "KubernetesTemplateExtensionConfig",
    "get_kubernetes_extension_defaults",
    "initialize_kubernetes_provider",
    "is_kubernetes_provider_registered",
    "register_kubernetes_provider",
]
