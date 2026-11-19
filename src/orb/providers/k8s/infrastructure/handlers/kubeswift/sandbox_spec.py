"""Build a ``SwiftSandbox`` spec from an ORB template.

KubeSwift settings live in the template's ``metadata.kubeswift`` block.
``metadata`` is a free dict on ORB's generic template, so it survives
template loading, while the typed ``provider_config`` drops keys it does not
know.

A ``SwiftSandbox`` is not a pod, so a template that sets a pod-only field is
refused rather than silently ignored.
"""

from __future__ import annotations

from typing import Any, Optional

# K8sTemplate fields that shape a pod and mean nothing to a SwiftSandbox.
POD_ONLY_FIELDS: tuple[str, ...] = (
    "resource_requests",
    "resource_limits",
    "pod_spec_override",
    "native_spec",
    "native_spec_path",
    "security_context",
    "volumes",
    "volume_mounts",
    "readiness_probe",
    "liveness_probe",
    "service_account",
    "runtime_class",
    "tolerations",
    "priority_class_name",
    "restart_policy",
    "completions",
    "parallelism",
)

# Fields a pooled checkout inherits from its SwiftSandboxPool.  A checkout
# whose shape differs from the pool's boots cold instead of taking a warm
# slot, so the spec copies them from the pool and a template that
# contradicts the pool is refused.
POOL_SHAPE_KEYS: tuple[str, ...] = (
    "image",
    "cpu",
    "memory",
    "network",
    "nodeSelector",
    "rootfsMode",
)

# Optional keys copied from the kubeswift block onto a cold-boot spec as-is.
_COLD_PASSTHROUGH: tuple[str, ...] = (
    "nodeSelector",
    "gpuProfileRef",
    "gpuResourceClaim",
    "scratchDisk",
    "model",
    "securityContext",
)

_DEFAULT_MEMORY = "512Mi"


class KubeSwiftTemplateError(ValueError):
    """The template cannot be turned into a ``SwiftSandbox``."""


def kubeswift_config(template: Any) -> dict[str, Any]:
    """Return the template's ``metadata.kubeswift`` block, or ``{}``."""
    # getattr: accepts a generic Template, a K8sTemplate or a test double.
    metadata = getattr(template, "metadata", None) or {}
    block = metadata.get("kubeswift") if isinstance(metadata, dict) else None
    return block if isinstance(block, dict) else {}


def _is_set(value: Any) -> bool:
    return value not in (None, [], {}, "")


def static_errors(
    *,
    image: Optional[str],
    machine_bootstrap: Optional[str],
    kubeswift: Any,
    pod_fields: dict[str, Any],
) -> list[str]:
    """Rules that need no cluster, shared by ``orb templates validate`` and acquire."""
    errors: list[str] = []
    if kubeswift is not None and not isinstance(kubeswift, dict):
        return ["metadata.kubeswift must be an object"]
    block = kubeswift or {}
    if machine_bootstrap:
        errors.append("KubeSwiftSandbox has no cloud-init; unset machine_bootstrap")
    bad = [name for name in POD_ONLY_FIELDS if _is_set(pod_fields.get(name))]
    if bad:
        errors.append(f"pod fields do not apply to a SwiftSandbox: {', '.join(bad)}")
    if not image:
        errors.append("machine_image is required (for a pooled sandbox, the pool's image)")
    if block.get("poolRef") and (block.get("gpuProfileRef") or block.get("gpuResourceClaim")):
        errors.append("a pooled sandbox takes its GPU from the pool; unset the GPU field")
    if block.get("gpuProfileRef") and block.get("gpuResourceClaim"):
        errors.append("gpuProfileRef and gpuResourceClaim are mutually exclusive")
    env = block.get("env")
    if env is not None and not isinstance(env, dict):
        errors.append("metadata.kubeswift.env must be an object of name: value")
    return errors


def template_static_errors(template: Any) -> list[str]:
    """:func:`static_errors` for a template object."""
    # getattr throughout this function: accepts a generic Template, a
    # K8sTemplate or a raw object from the validator's best-effort coercion.
    metadata = getattr(template, "metadata", None) or {}
    return static_errors(
        image=getattr(template, "machine_image", None),
        machine_bootstrap=getattr(template, "machine_bootstrap", None),
        kubeswift=metadata.get("kubeswift") if isinstance(metadata, dict) else None,
        pod_fields={name: getattr(template, name, None) for name in POD_ONLY_FIELDS},
    )


def build_spec(template: Any, pool: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Return the ``SwiftSandbox`` spec for one machine.

    Args:
        template: The ORB template.
        pool: The ``SwiftSandboxPool`` named by ``metadata.kubeswift.poolRef``,
            or ``None`` for a cold boot.

    Raises:
        KubeSwiftTemplateError: When the template cannot be honoured.
    """
    # getattr throughout this function: accepts a generic Template, a
    # K8sTemplate or a test double; pod fields may be absent on the generic one.
    errors = template_static_errors(template)
    if errors:
        raise KubeSwiftTemplateError("; ".join(errors))
    block = kubeswift_config(template)
    image = getattr(template, "machine_image", None)

    spec: dict[str, Any] = {}
    if pool is not None:
        pool_spec = pool.get("spec") or {}
        for key in POOL_SHAPE_KEYS:
            if key in pool_spec:
                spec[key] = pool_spec[key]
        if image != pool_spec.get("image"):
            raise KubeSwiftTemplateError(
                f"machine_image {image!r} differs from the pool image {pool_spec.get('image')!r}"
            )
        for key in ("cpu", "memory", "network", "nodeSelector"):
            if key in block and block[key] != pool_spec.get(key):
                raise KubeSwiftTemplateError(
                    f"{key} {block[key]!r} differs from the pool's {pool_spec.get(key)!r}; "
                    "a checkout cannot change the slot shape"
                )
        spec["poolRef"] = {"name": pool["metadata"]["name"]}
    else:
        spec["image"] = image
        spec["memory"] = block.get("memory", _DEFAULT_MEMORY)
        if block.get("cpu"):
            spec["cpu"] = int(block["cpu"])
        spec["network"] = block.get("network") or {"mode": "restricted"}
        for key in _COLD_PASSTHROUGH:
            if _is_set(block.get(key)):
                spec[key] = block[key]
        if isinstance(spec.get("gpuProfileRef"), str):
            spec["gpuProfileRef"] = {"name": spec["gpuProfileRef"]}

    command = block.get("command") or getattr(template, "command", None)
    if command:
        spec["command"] = list(command)
    args = block.get("args") or getattr(template, "args", None)
    if args:
        spec["args"] = list(args)
    env = block.get("env") or {}
    if env:
        spec["env"] = [{"name": str(k), "value": str(v)} for k, v in env.items()]
    for key in ("timeout", "ttl", "workingDir"):
        if block.get(key):
            spec[key] = block[key]
    # Never set spec.podMetadata labels: KubeSwift copies them onto the
    # launcher pod, and ORB's pod orphan GC would then treat the launcher of
    # a live sandbox as its own pod.
    return spec


__all__ = [
    "POD_ONLY_FIELDS",
    "POOL_SHAPE_KEYS",
    "KubeSwiftTemplateError",
    "build_spec",
    "kubeswift_config",
    "static_errors",
    "template_static_errors",
]
