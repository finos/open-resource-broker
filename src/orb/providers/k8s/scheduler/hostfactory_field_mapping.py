"""Kubernetes implementation of ``FieldMappingPort`` for the HostFactory scheduler.

Mirrors :class:`orb.providers.aws.scheduler.hostfactory_field_mapping.AWSFieldMapping`
but maps the kubernetes-specific HostFactory template fields (camelCase) to
the internal snake_case names consumed by the provider strategy and the
Pod / Deployment / StatefulSet / Job handlers.

The shared ``HostFactoryFieldMappings.MAPPINGS["generic"]`` table is applied
first by the HostFactory field mapper; this adapter contributes only the
kubernetes-specific entries that should be merged on top.

Registered with :class:`FieldMappingRegistry` during provider bootstrap in
:mod:`orb.providers.k8s.registration`.
"""

from __future__ import annotations

from typing import Optional

from orb.infrastructure.scheduler.hostfactory.field_mapping_port import FieldMappingPort


class K8sFieldMapping:
    """Kubernetes-specific field-mapping adapter for the HostFactory scheduler."""

    # Kubernetes-specific HF field -> internal field mappings.
    # Generic mappings (templateId, maxNumber, imageId, ...) live in the
    # shared ``HostFactoryFieldMappings.MAPPINGS["generic"]`` table; this
    # dict carries only the kubernetes-specific additions.  Shadow
    # fields (``containerImage`` / ``labels`` / ``replicas``) are
    # intentionally absent — HF callers should use the generic
    # ``imageId``, ``tags`` and ``maxNumber`` surfaces respectively.
    _PROVIDER_MAPPINGS: dict[str, str] = {
        # Scheduling / placement
        "namespace": "namespace",
        "namespaces": "namespaces",
        "runtimeClass": "runtime_class",
        "nodeSelector": "node_selector",
        "tolerations": "tolerations",
        "serviceAccount": "service_account",
        "serviceName": "service_name",
        # Resource requests / limits
        "resourceRequests": "resource_requests",
        "resourceLimits": "resource_limits",
        # Workload sizing overrides for the Job handler
        "completions": "completions",
        "parallelism": "parallelism",
        # Pod metadata
        "annotations": "annotations",
        # Storage / runtime
        "volumeMounts": "volume_mounts",
        "volumes": "volumes",
        # Container environment
        "env": "env",
        "environment": "env",
        # Container entrypoint override
        "command": "command",
        "args": "args",
        # Image pull
        "imagePullSecret": "image_pull_secret",
        # Raw partial pod-spec override
        "podSpecOverride": "pod_spec_override",
        # Pod scheduling priority
        "priorityClassName": "priority_class_name",
        # Pod termination
        "terminationGracePeriodSeconds": "termination_grace_period_seconds",
        # Pod restart policy
        "restartPolicy": "restart_policy",
        # Container health probes
        "readinessProbe": "readiness_probe",
        "livenessProbe": "liveness_probe",
        # Pod-level security context
        "securityContext": "security_context",
        # Job lifecycle
        "ttlSecondsAfterFinished": "ttl_seconds_after_finished",
        "activeDeadlineSeconds": "active_deadline_seconds",
        # Native-spec file path escape hatch
        "nativeSpecPath": "native_spec_path",
    }

    def get_mappings(self) -> dict[str, str]:
        """Return the kubernetes-specific HF-field -> internal-field name entries."""
        return dict(self._PROVIDER_MAPPINGS)

    def apply_defaults(self, mapped: dict) -> dict:
        """Apply kubernetes-specific ``setdefault`` logic after field mapping.

        Mutates *mapped* in place and returns it for convenience.  Defaults:

        * ``max_machines`` -> ``1`` (generic quota cap).
        * ``annotations`` -> empty dict.

        ``namespace`` is intentionally NOT defaulted here.  Doing so would
        force a hardcoded value onto the template, which then takes
        precedence over the provider-config namespace at
        :meth:`K8sBaseHandler.resolve_namespace` time and silently
        overrides the operator's configured default.  The precedence
        order resolved at handler time is: HF/template ``namespace`` ->
        ``K8sProviderConfig.namespace`` -> kube-API default ``"default"``.

        Replica count is taken from ``request.requested_count`` at acquire
        time, not from the template, so ``replicas`` is intentionally not
        defaulted here.  Operator labels live on the generic ``tags``
        field on :class:`Template`.
        """
        mapped.setdefault("max_machines", 1)
        mapped.setdefault("annotations", {})
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
_: FieldMappingPort = K8sFieldMapping()  # type: ignore[assignment]
