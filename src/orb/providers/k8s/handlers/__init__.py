"""Kubernetes provider handlers.

Mirrors :mod:`orb.providers.aws.infrastructure.handlers` in shape — the
handler ABC plus concrete handlers, one per provider-API key:

* :class:`K8sHandlerBase`         — abstract base
* :class:`K8sPodHandler`          — ``provider_api="Pod"``
* :class:`K8sDeploymentHandler`   — ``provider_api="Deployment"``
* :class:`K8sStatefulSetHandler`  — ``provider_api="StatefulSet"``
* :class:`K8sJobHandler`          — ``provider_api="Job"``
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
