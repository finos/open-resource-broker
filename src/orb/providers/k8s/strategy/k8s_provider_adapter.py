"""Kubernetes provider adapter — state mapper and resource validator.

Mirrors :mod:`orb.providers.aws.strategy.aws_provider_adapter` in role.

``K8sStateMapper`` translates Kubernetes pod-phase / container-status
strings to the provider-agnostic :class:`ProviderInstanceState` enum used
by the application layer and the machine-sync service.

The state mapping is intentionally conservative:

* Any phase or status not explicitly listed falls back to
  ``ProviderInstanceState.UNKNOWN`` rather than raising, so new pod
  phases introduced in future Kubernetes releases are handled gracefully.
* ``"Succeeded"`` maps to ``STOPPED`` (the pod completed successfully —
  it is no longer running but was not forcibly terminated).
* ``"Failed"`` maps to ``STOPPED`` for the same reason (terminal, not
  killed by ORB).
* ``"Unknown"`` (cluster-level — node unreachable) maps to ``UNKNOWN``.

``K8sProviderAdapter`` is the ``@injectable`` facade that wraps the mapper
and any future k8s-specific resource validators, mirroring the structure
of ``AWSProviderAdapter``.
"""

from __future__ import annotations

from typing import Any

from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_interfaces import (
    ProviderInstanceState,
    ProviderResourceIdentifier,
    ProviderResourceTag,
    ProviderResourceValidator,
    ProviderStateMapper,
)
from orb.infrastructure.di.injectable import injectable


class K8sStateMapper:
    """Map Kubernetes pod-phase / workload-condition strings to domain state."""

    # Pod phase → domain state.
    _PHASE_MAP: dict[str, ProviderInstanceState] = {
        "Pending": ProviderInstanceState.PENDING,
        "Running": ProviderInstanceState.RUNNING,
        "Succeeded": ProviderInstanceState.STOPPED,
        "Failed": ProviderInstanceState.STOPPED,
        "Unknown": ProviderInstanceState.UNKNOWN,
    }

    # ORB internal status strings (emitted by PodStatusResolver / handlers)
    # → domain state.
    _ORB_STATUS_MAP: dict[str, ProviderInstanceState] = {
        "pending": ProviderInstanceState.PENDING,
        "starting": ProviderInstanceState.PENDING,
        "running": ProviderInstanceState.RUNNING,
        "succeeded": ProviderInstanceState.STOPPED,
        "terminated": ProviderInstanceState.TERMINATED,
        "failed": ProviderInstanceState.STOPPED,
        "unknown": ProviderInstanceState.UNKNOWN,
    }

    def map_to_domain_state(self, k8s_state: str) -> ProviderInstanceState:
        """Map a Kubernetes pod phase or ORB status string to domain state.

        Tries the phase map first (title-case Kubernetes values), then the
        ORB internal status map (lowercase).  Returns
        ``ProviderInstanceState.UNKNOWN`` for unrecognised strings.
        """
        mapped = self._PHASE_MAP.get(k8s_state)
        if mapped is not None:
            return mapped
        mapped = self._ORB_STATUS_MAP.get(k8s_state.lower())
        if mapped is not None:
            return mapped
        return ProviderInstanceState.UNKNOWN

    def map_from_domain_state(self, domain_state: ProviderInstanceState) -> str:
        """Map a domain state back to an ORB k8s status string.

        Used for serialisation; returns lower-case ORB status strings.
        """
        _reverse: dict[ProviderInstanceState, str] = {
            ProviderInstanceState.PENDING: "pending",
            ProviderInstanceState.RUNNING: "running",
            ProviderInstanceState.STOPPING: "running",
            ProviderInstanceState.STOPPED: "succeeded",
            ProviderInstanceState.SHUTTING_DOWN: "terminated",
            ProviderInstanceState.TERMINATED: "terminated",
            ProviderInstanceState.UNKNOWN: "unknown",
        }
        return _reverse.get(domain_state, "unknown")


class K8sResourceValidator:
    """Kubernetes resource identifier and tag validator."""

    def validate_resource_identifier(self, identifier: str, resource_type: str) -> bool:
        """Return True when *identifier* is non-empty and looks like a k8s name."""
        if not identifier or not identifier.strip():
            return False
        # Kubernetes resource names are RFC 1123 DNS labels / subdomains.
        # We do a lightweight check: non-empty, ≤ 253 chars, valid characters.
        import re

        if len(identifier) > 253:
            return False
        return bool(re.match(r"^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?$", identifier))

    def validate_tag(self, tag: ProviderResourceTag) -> bool:
        """Return True when *tag* satisfies Kubernetes label key/value rules."""
        if not tag.key or len(tag.key) > 316:  # 253 prefix + "/" + 63 name
            return False
        if len(tag.value) > 63:
            return False
        return True

    def validate_launch_template(self, template: Any) -> bool:  # type: ignore[override]
        """Not applicable for Kubernetes — always returns False."""
        return False


@injectable
class K8sProviderAdapter:
    """Kubernetes provider adapter — wraps state mapper and resource validator."""

    def __init__(self, logger: LoggingPort) -> None:
        self._state_mapper = K8sStateMapper()
        self._resource_validator = K8sResourceValidator()
        self._logger = logger

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return "k8s"

    @property
    def state_mapper(self) -> ProviderStateMapper:
        """Return the k8s → domain state mapper."""
        return self._state_mapper  # type: ignore[return-value]

    @property
    def resource_validator(self) -> ProviderResourceValidator:
        """Return the k8s resource validator."""
        return self._resource_validator

    def create_resource_identifier(
        self,
        resource_type: str,
        identifier: str,
        namespace: str | None = None,
    ) -> ProviderResourceIdentifier:
        """Create a k8s resource identifier, validating the name format."""
        if not self._resource_validator.validate_resource_identifier(identifier, resource_type):
            self._logger.warning("Invalid k8s %s identifier: %s", resource_type, identifier)
            raise ValueError(f"Invalid k8s {resource_type} identifier: {identifier}")
        return ProviderResourceIdentifier(
            provider_type="k8s",
            resource_type=resource_type,
            identifier=identifier,
            region=namespace,
        )


__all__ = ["K8sProviderAdapter", "K8sStateMapper", "K8sResourceValidator"]
