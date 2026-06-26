"""Kubernetes provider handlers.

Mirrors :mod:`orb.providers.aws.infrastructure.handlers` in shape — the
handler ABC plus concrete handlers, one per provider-API key.  Phase B
introduces :class:`K8sHandlerBase` and :class:`K8sPodHandler`;
Phase D adds :class:`K8sDeploymentHandler`; Phase E adds
:class:`K8sStatefulSetHandler` and :class:`K8sJobHandler`.
"""

from orb.providers.k8s.handlers.base_handler import K8sHandlerBase
from orb.providers.k8s.handlers.deployment_handler import K8sDeploymentHandler
from orb.providers.k8s.handlers.job_handler import K8sJobHandler
from orb.providers.k8s.handlers.pod_handler import K8sPodHandler
from orb.providers.k8s.handlers.statefulset_handler import K8sStatefulSetHandler

__all__ = [
    "K8sDeploymentHandler",
    "K8sHandlerBase",
    "K8sJobHandler",
    "K8sPodHandler",
    "K8sStatefulSetHandler",
]
