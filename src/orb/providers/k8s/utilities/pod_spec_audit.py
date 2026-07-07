"""Pod-spec security audit helpers.

Inspects a rendered Kubernetes pod spec (as a plain ``dict``) for
high-risk fields — ``hostNetwork``, ``hostPID``, ``hostIPC``,
``hostPath`` volumes, privileged containers, dangerous capabilities, etc.
— and logs each finding at ``WARNING`` level so operators see what they
are submitting before the pod reaches the apiserver.

RBAC gates the actual abuse; this module provides **observability**, not
a hard policy boundary.  Policy enforcement is opt-in via
:attr:`K8sProviderConfig.reject_high_risk_pod_fields`.

Usage::

    from orb.providers.k8s.utilities.pod_spec_audit import audit_pod_spec

    warnings = audit_pod_spec(rendered_spec, logger)
    # Each finding is already logged at WARNING level by the function.
    # ``warnings`` is returned so callers can decide to raise on findings.

Key normalisation
-----------------
The function accepts spec dicts with **camelCase** keys (e.g. a dict
rendered from a Jinja native-spec template) *and* **snake_case** keys
(e.g. the output of ``kubernetes.client.V1Pod.to_dict()``).  Every
field is looked up under both forms so callers do not need to normalise
before calling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — type-checking only
    from orb.domain.base.ports import LoggingPort

# Capabilities whose presence in ``securityContext.capabilities.add``
# warrants a warning.
_DANGEROUS_CAPABILITIES: frozenset[str] = frozenset(
    {
        "SYS_ADMIN",
        "NET_ADMIN",
        "NET_RAW",
    }
)


def _get(d: dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    """Look up a key by camelCase first, then snake_case.

    Returns the first non-``None`` value found, or *default* when neither
    key is present.  This lets the audit function handle both native-spec
    dicts (camelCase) and SDK ``.to_dict()`` output (snake_case) without
    pre-processing.
    """
    v = d.get(camel)
    if v is not None:
        return v
    return d.get(snake, default)


def audit_pod_spec(pod_spec: dict[str, Any], logger: "LoggingPort") -> list[str]:
    """Audit *pod_spec* for high-risk fields and log each finding.

    The function accepts:

    * The bare ``spec`` sub-dict of a pod manifest
      (``{"hostNetwork": True, "containers": [...]}`` or its snake_case
      equivalent ``{"host_network": True, ...}``).
    * The full pod manifest
      (``{"apiVersion": ..., "spec": {...}}``).

    Both camelCase (native-spec / Jinja-rendered) and snake_case
    (``kubernetes.client.V1Pod.to_dict()``) key formats are handled.

    Audited fields
    --------------
    * ``spec.hostNetwork`` / ``host_network`` == ``True``
    * ``spec.hostPID`` / ``host_pid`` == ``True``
    * ``spec.hostIPC`` / ``host_ipc`` == ``True``
    * ``spec.volumes[*].hostPath`` / ``host_path`` (any non-empty entry)
    * ``spec.containers[*].securityContext.privileged`` == ``True``
    * ``spec.containers[*].securityContext.allowPrivilegeEscalation`` /
      ``allow_privilege_escalation`` == ``True``
    * ``spec.containers[*].securityContext.runAsUser`` / ``run_as_user``
      == ``0``
    * ``spec.containers[*].securityContext.capabilities.add`` containing
      ``"SYS_ADMIN"``, ``"NET_ADMIN"``, or ``"NET_RAW"``

    The same checks are applied to ``spec.initContainers`` /
    ``init_containers``.

    Parameters
    ----------
    pod_spec:
        The pod spec dict (see above for accepted shapes).
    logger:
        A :class:`orb.domain.base.ports.LoggingPort` instance.
        One ``warning(...)`` call is emitted per finding.

    Returns
    -------
    list[str]
        Human-readable warning messages, one per finding.  Empty when
        the spec contains no high-risk fields.
    """
    if not isinstance(pod_spec, dict):
        return []

    # Normalise: if the caller passed the full pod manifest, descend into
    # the ``spec`` sub-dict.  Bare spec dicts pass through unchanged.
    spec: dict[str, Any] = pod_spec
    if "apiVersion" in pod_spec or "api_version" in pod_spec or "kind" in pod_spec:
        spec = pod_spec.get("spec") or {}

    findings: list[str] = []

    # ------------------------------------------------------------------
    # Host-namespace flags
    # ------------------------------------------------------------------
    for camel, snake, path in (
        ("hostNetwork", "host_network", "spec.hostNetwork"),
        ("hostPID", "host_pid", "spec.hostPID"),
        ("hostIPC", "host_ipc", "spec.hostIPC"),
    ):
        if _get(spec, camel, snake) is True:
            msg = f"high-risk pod-spec field detected: {path} = True"
            logger.warning("WARN: %s", msg)
            findings.append(msg)

    # ------------------------------------------------------------------
    # hostPath volumes
    # ------------------------------------------------------------------
    volumes = _get(spec, "volumes", "volumes") or []
    for i, volume in enumerate(volumes):
        if not isinstance(volume, dict):
            continue
        host_path = volume.get("hostPath") or volume.get("host_path")
        if host_path:
            path_value = (
                host_path.get("path", "") if isinstance(host_path, dict) else str(host_path)
            )
            vol_name = volume.get("name", f"volume[{i}]")
            msg = (
                f"high-risk pod-spec field detected: "
                f"spec.volumes[{i}].hostPath ({vol_name}) = {path_value!r}"
            )
            logger.warning("WARN: %s", msg)
            findings.append(msg)

    # ------------------------------------------------------------------
    # Per-container security context checks
    # ------------------------------------------------------------------
    def _check_containers(
        containers: list[Any],
        section: str,
    ) -> None:
        for ci, container in enumerate(containers):
            if not isinstance(container, dict):
                continue
            container_name = container.get("name", f"{section}[{ci}]")
            sc = container.get("securityContext") or container.get("security_context") or {}
            if not isinstance(sc, dict):
                continue

            if sc.get("privileged") is True:
                msg = (
                    f"high-risk pod-spec field detected: "
                    f"spec.{section}[{ci}] ({container_name}).securityContext.privileged = True"
                )
                logger.warning("WARN: %s", msg)
                findings.append(msg)

            if _get(sc, "allowPrivilegeEscalation", "allow_privilege_escalation") is True:
                msg = (
                    f"high-risk pod-spec field detected: "
                    f"spec.{section}[{ci}] ({container_name})"
                    f".securityContext.allowPrivilegeEscalation = True"
                )
                logger.warning("WARN: %s", msg)
                findings.append(msg)

            if _get(sc, "runAsUser", "run_as_user") == 0:
                msg = (
                    f"high-risk pod-spec field detected: "
                    f"spec.{section}[{ci}] ({container_name}).securityContext.runAsUser = 0"
                )
                logger.warning("WARN: %s", msg)
                findings.append(msg)

            caps = sc.get("capabilities") or {}
            if isinstance(caps, dict):
                added: list[str] = caps.get("add") or []
                for cap in added:
                    if str(cap).upper() in _DANGEROUS_CAPABILITIES:
                        msg = (
                            f"high-risk pod-spec field detected: "
                            f"spec.{section}[{ci}] ({container_name})"
                            f".securityContext.capabilities.add contains {cap!r}"
                        )
                        logger.warning("WARN: %s", msg)
                        findings.append(msg)

    containers = _get(spec, "containers", "containers") or []
    _check_containers(containers, "containers")

    init_containers = _get(spec, "initContainers", "init_containers") or []
    _check_containers(init_containers, "initContainers")

    return findings


__all__ = ["audit_pod_spec"]
