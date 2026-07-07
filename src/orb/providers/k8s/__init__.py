"""Kubernetes provider implementation.

All direct ``kubernetes`` SDK imports are confined to this subtree (enforced by
the architecture test in ``tests/unit/architecture/test_k8s_leak_detection.py``).
"""

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
from orb.providers.k8s.strategy.k8s_provider_strategy import (
    K8sProviderStrategy,
)

__all__: list[str] = [
    "K8sProviderConfig",
    "K8sProviderStrategy",
    "K8sTemplateExtensionConfig",
    "get_k8s_extension_defaults",
    "initialize_k8s_provider",
    "is_k8s_provider_registered",
    "register_k8s_provider",
]
