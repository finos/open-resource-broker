"""Shared utilities for Kubernetes provider handlers.

Mirrors :mod:`orb.providers.aws.infrastructure.handlers.shared` in role.
Provides reusable building-blocks that each concrete handler consumes:

* :mod:`.namespace_resolver` — namespace resolution from template and config
* :mod:`.label_stamper`      — per-request identity stamping on workload bodies
* :mod:`.pod_state_translator` — pod SDK object → ORB instance-dict translation
"""

from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
    stamp_native_workload_body,
)
from orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver import (
    resolve_namespace,
    resolve_namespace_from_provider_data,
)
from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
    instance_dict_for_pod,
    instance_dict_for_state,
)

__all__ = [
    "instance_dict_for_pod",
    "instance_dict_for_state",
    "resolve_namespace",
    "resolve_namespace_from_provider_data",
    "stamp_native_workload_body",
]
