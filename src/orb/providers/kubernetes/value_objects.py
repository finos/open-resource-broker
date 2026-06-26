"""Public Kubernetes provider value objects.

Phase A introduces the provider-api enum stub.  Later phases (B, E, F)
fill in the handler-side template aggregate types under
``orb.providers.kubernetes.domain.template``.
"""

from __future__ import annotations

from enum import Enum


class KubernetesProviderApi(str, Enum):
    """Canonical provider API identifiers for the kubernetes provider.

    Mirrors the AWS provider's
    :class:`orb.providers.aws.domain.template.value_objects.ProviderApi`
    enum.  Each value maps one-to-one to a handler implementation that
    arrives in subsequent phases:

    * ``KubernetesPod``         — Phase B
    * ``KubernetesDeployment``  — Phase E
    * ``KubernetesStatefulSet`` — Phase E
    * ``KubernetesJob``         — Phase F
    """

    POD = "KubernetesPod"
    DEPLOYMENT = "KubernetesDeployment"
    STATEFUL_SET = "KubernetesStatefulSet"
    JOB = "KubernetesJob"


__all__: list[str] = ["KubernetesProviderApi"]
