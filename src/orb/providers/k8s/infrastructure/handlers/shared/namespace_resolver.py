"""Namespace resolution helpers for Kubernetes provider handlers.

Provides the namespace-resolution logic that every concrete handler needs
so that future handlers can call these functions directly rather than
duplicating the logic in a growing :class:`K8sHandlerBase`.

The resolution chain is:

1. Per-template override  — ``K8sTemplate.namespace`` when set.
2. Provider default       — ``K8sProviderConfig.namespace``.

When the provider config defines a ``namespaces`` allowlist the resolved
value is validated against it.  A wildcard ``["*"]`` entry bypasses the
check.
"""

from __future__ import annotations

from typing import Any, Optional

from orb.providers.k8s.configuration.config import K8sProviderConfig


def resolve_namespace(
    template: Any,
    config: K8sProviderConfig,
) -> str:
    """Return the namespace this request should target.

    Resolution order:

    1. :attr:`K8sTemplate.namespace` if set (per-template override).
    2. ``K8sProviderConfig.namespace`` (provider default).

    When the provider config has an explicit ``namespaces`` list the
    resolved namespace MUST appear in the list — otherwise a
    :class:`ValueError` is raised.  ``namespaces=["*"]`` is a wildcard
    and is never rejected.

    Args:
        template: A :class:`~orb.domain.template.template_aggregate.Template`
            or :class:`~orb.providers.k8s.domain.template.k8s_template.K8sTemplate`
            instance.
        config: The provider configuration for the target cluster.

    Returns:
        The resolved namespace as a non-empty string.

    Raises:
        ValueError: When the resolved namespace is not in the configured
            ``namespaces`` allowlist.
    """
    from orb.providers.k8s.domain.template.k8s_template import (  # noqa: PLC0415
        upcast_to_k8s_template,
    )

    k8s_template = upcast_to_k8s_template(template)
    candidate: Optional[str] = k8s_template.namespace if k8s_template.namespace else None
    if candidate is None:
        candidate = config.namespace

    assert candidate is not None, "namespace must be resolved by model_validator"

    # Validate the resolved namespace against RFC 1123 DNS label rules
    # before constructing any API request.  This guards against requests
    # that carry a malformed or injection-capable namespace string.
    try:
        from orb.providers.k8s.utilities.labels import (  # noqa: PLC0415
            validate_namespace as _validate_ns,
        )

        _validate_ns(candidate)
    except Exception as _ns_err:
        from orb.providers.k8s.exceptions.k8s_errors import K8sError  # noqa: PLC0415

        raise K8sError(
            f"Resolved namespace {candidate!r} is not a valid Kubernetes namespace: {_ns_err}"
        ) from _ns_err

    allowed = config.namespaces
    if allowed and allowed != ["*"] and candidate not in allowed:
        raise ValueError(
            f"Namespace {candidate!r} is not in the provider's configured "
            f"namespaces list {allowed!r}.  Update the template or the "
            "provider config."
        )
    return candidate


def resolve_namespace_from_provider_data(
    provider_data: dict[str, Any],
    config: K8sProviderConfig,
) -> str:
    """Resolve a namespace from a ``provider_data`` dict.

    Reads the ``namespace`` key written by ``acquire_hosts``; falls back to
    the provider's default namespace when the key is absent or empty.  The
    ``_resolve_namespace`` model_validator on :class:`K8sProviderConfig`
    guarantees the default is always a non-empty string.

    Args:
        provider_data: The ``provider_data`` dict stored on the Request
            aggregate by ``acquire_hosts``.
        config: The provider configuration for the target cluster.

    Returns:
        The resolved namespace as a non-empty string.
    """
    ns = provider_data.get("namespace")
    if isinstance(ns, str) and ns:
        return ns
    namespace = config.namespace
    assert namespace is not None, "namespace must be resolved by model_validator"
    return namespace


__all__ = [
    "resolve_namespace",
    "resolve_namespace_from_provider_data",
]
