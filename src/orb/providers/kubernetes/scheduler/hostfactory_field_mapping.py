"""Kubernetes implementation of ``FieldMappingPort`` for the HostFactory scheduler.

Mirrors :class:`orb.providers.aws.scheduler.hostfactory_field_mapping.AWSFieldMapping`
but maps the kubernetes-specific HostFactory template fields (camelCase) to
the internal snake_case names consumed by the provider strategy and the
Pod / Deployment / StatefulSet / Job handlers.

The shared ``HostFactoryFieldMappings.MAPPINGS["generic"]`` table is applied
first by the HostFactory field mapper; this adapter contributes only the
kubernetes-specific entries that should be merged on top.

Registered with :class:`FieldMappingRegistry` during provider bootstrap in
:mod:`orb.providers.kubernetes.registration`.
"""

from __future__ import annotations

from typing import Optional

from orb.infrastructure.scheduler.hostfactory.field_mapping_port import FieldMappingPort


class KubernetesFieldMapping:
    """Kubernetes-specific field-mapping adapter for the HostFactory scheduler."""

    # Kubernetes-specific HF field -> internal field mappings.
    # Generic mappings (templateId, maxNumber, etc.) live in the shared
    # ``HostFactoryFieldMappings.MAPPINGS["generic"]`` table; this dict carries
    # only the kubernetes-specific additions.
    _PROVIDER_MAPPINGS: dict[str, str] = {
        # Container image / namespace / scheduling
        "containerImage": "container_image",
        "namespace": "namespace",
        "runtimeClass": "runtime_class",
        "nodeSelector": "node_selector",
        "tolerations": "tolerations",
        "serviceAccount": "service_account",
        # Resource requests / limits
        "resourceRequests": "resource_requests",
        "resourceLimits": "resource_limits",
        # Workload sizing for controller-backed handlers
        "completions": "completions",
        "parallelism": "parallelism",
        "replicas": "replicas",
        # Pod metadata
        "labels": "labels",
        "annotations": "annotations",
        # Storage / runtime
        "volumeMounts": "volume_mounts",
        "volumes": "volumes",
        # Container environment
        "env": "environment_variables",
        "environment": "environment_variables",
    }

    def get_mappings(self) -> dict[str, str]:
        """Return the kubernetes-specific HF-field -> internal-field name entries."""
        return dict(self._PROVIDER_MAPPINGS)

    def apply_defaults(self, mapped: dict) -> dict:
        """Apply kubernetes-specific ``setdefault`` logic after field mapping.

        Mutates *mapped* in place and returns it for convenience.  Defaults:

        * ``namespace`` -> ``"default"`` (matches the kube-API default; the
          provider-level config overrides this when set).
        * ``max_instances`` -> ``1``.
        * ``replicas`` -> the resolved ``max_instances`` for controller-backed
          handlers that read this field.  We only set the default when
          ``replicas`` is absent so an explicit HF value wins.
        * ``labels`` -> empty dict.
        * ``annotations`` -> empty dict.
        * ``environment_variables`` -> empty dict.
        """
        mapped.setdefault("namespace", "default")
        mapped.setdefault("max_instances", 1)
        mapped.setdefault("replicas", mapped["max_instances"])
        mapped.setdefault("labels", {})
        mapped.setdefault("annotations", {})
        mapped.setdefault("environment_variables", {})
        return mapped

    def derive_attributes(self, machine_type: str | None) -> Optional[dict[str, list[str]]]:
        """The kubernetes provider does not infer cpu/ram from a machine-type string.

        Pods declare ``resource_requests`` / ``resource_limits`` directly, so
        there is no analogue of the AWS instance-type catalogue from which we
        could derive HF ``ncpus`` / ``nram`` attributes.  Returning ``None``
        lets the caller fall back gracefully.
        """
        return None


# Verify the class satisfies the protocol at import time.
_: FieldMappingPort = KubernetesFieldMapping()  # type: ignore[assignment]
