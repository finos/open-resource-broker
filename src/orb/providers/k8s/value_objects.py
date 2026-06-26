"""Public Kubernetes provider value objects.

Exposes the provider-api enum that callers route on.  Handler-side
template aggregate types live alongside their respective handlers under
``orb.providers.k8s.domain.template``.
"""

from __future__ import annotations

from enum import Enum


class KubernetesProviderApi(str, Enum):
    """Canonical provider API identifiers for the kubernetes provider.

    Mirrors the AWS provider's
    :class:`orb.providers.aws.domain.template.value_objects.ProviderApi`
    enum.  Each value maps one-to-one to a concrete handler class:

    * ``Pod``         — :class:`K8sPodHandler`
    * ``Deployment``  — :class:`K8sDeploymentHandler`
    * ``StatefulSet`` — :class:`K8sStatefulSetHandler`
    * ``Job``         — :class:`K8sJobHandler`
    """

    POD = "Pod"
    DEPLOYMENT = "Deployment"
    STATEFUL_SET = "StatefulSet"
    JOB = "Job"


__all__: list[str] = ["KubernetesProviderApi"]
