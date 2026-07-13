"""Kubernetes template validator — registration-time structural checks.

Validates :class:`~orb.domain.template.template_aggregate.Template` (and its
:class:`~orb.providers.k8s.domain.template.k8s_template_aggregate.K8sTemplate`
subclass) against the rules that can be evaluated entirely from the template
data itself, without any live Kubernetes API contact.

The validator is returned by
:func:`~orb.providers.k8s.registration.create_k8s_validator` and called by
the provider registry at template-registration time so that malformed
templates are rejected before the first acquire attempt.

Validation rules
----------------
1. ``template_id`` — must be set and non-empty.
2. ``max_instances`` — must be a positive integer (>= 1).
3. ``provider_api`` — when set, must be one of ``{Pod, Deployment,
   StatefulSet, Job}``; absent means the runtime default (``Pod``) is used.
4. ``image_id`` — must be set and non-empty (the container image reference).
5. ``namespace`` — when set, must conform to the DNS-1123 label pattern
   (lowercase alphanumeric, hyphens permitted but not at start/end, max 63
   characters).
6. ``service_account`` — when set, must conform to the DNS-1123 subdomain
   pattern (lowercase alphanumeric, hyphens and dots permitted, must not
   start or end with a hyphen or dot, max 253 characters).  The Kubernetes
   ``serviceAccountName`` field accepts DNS-1123 subdomain names, so dotted
   names like ``default.sa`` are valid.
7. ``resource_requests`` / ``resource_limits`` — when set, every quantity
   string in the emitted resource map must parse cleanly via
   :func:`~orb.providers.k8s.utilities.quantity_parser.parse_cpu_quantity`
   (for CPU entries) or
   :func:`~orb.providers.k8s.utilities.quantity_parser.parse_memory_quantity`
   (for all other entries).
8. ``tolerations`` — when set, each entry must be parseable as a
   :class:`~orb.providers.k8s.domain.template.k8s_template_aggregate.K8sToleration`
   (Pydantic-backed, tolerates dict or model input).
9. ``restart_policy`` (when ``provider_api`` is set) — some ``restart_policy``
   values are invalid for specific workload kinds:

   * ``Job``: ``"Always"`` is rejected (kubelet refuses it; use ``"Never"``
     or ``"OnFailure"``).
   * ``Deployment`` / ``StatefulSet``: only ``"Always"`` or unset are valid
     (controllers force pods to restart; ``"Never"`` / ``"OnFailure"`` are
     incoherent for controller-managed pods).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from orb.providers.k8s.utilities.dns_names import (
    DNS_1123_LABEL_REGEX as _DNS_1123_LABEL,
    DNS_1123_SUBDOMAIN_MAX_LEN as _DNS_1123_SUBDOMAIN_MAX_LEN,
    DNS_1123_SUBDOMAIN_REGEX as _DNS_1123_SUBDOMAIN,
)

# ---------------------------------------------------------------------------
# Internal constants — mirrors the consts in template_adapter.py but kept
# local so this module has no hard import dependency on the adapter.
# ---------------------------------------------------------------------------

#: kubernetes resource-API types recognised by the v1 provider.
_SUPPORTED_PROVIDER_APIS: frozenset[str] = frozenset({"Pod", "Deployment", "StatefulSet", "Job"})

#: Kubernetes resource-quantity pattern (CPU / memory / storage).
#: Mirrors the regex in :mod:`template_adapter`.
_QUANTITY = re.compile(
    r"^[+-]?(\d+(\.\d+)?|\.\d+)"  # numeric magnitude
    r"([eE][+-]?\d+)?"  # optional exponent
    r"([numµ]|[kKMGTPE]i?)?$"  # SI / binary suffix
)

#: Template-config-dict keys (accepting both camelCase and snake_case) mapped
#: to the K8sTemplate field names, so a plain config dict handed to the
#: validator can be coerced to a typed K8sTemplate without the caller importing
#: any provider type.  Generic parent-template fields (template_id, image_id,
#: provider_api, max_instances) are forwarded verbatim.
_CONFIG_KEY_ALIASES: dict[str, str] = {
    "templateId": "template_id",
    "imageId": "image_id",
    "providerApi": "provider_api",
    "maxInstances": "max_instances",
    "maxNumber": "max_instances",
    "serviceAccount": "service_account",
    "serviceName": "service_name",
    "resourceRequests": "resource_requests",
    "resourceLimits": "resource_limits",
    "nodeSelector": "node_selector",
    "restartPolicy": "restart_policy",
}

_K8S_TEMPLATE_FIELDS: frozenset[str] = frozenset(
    {
        "template_id",
        "image_id",
        "provider_api",
        "max_instances",
        "namespace",
        "service_account",
        "service_name",
        "resource_requests",
        "resource_limits",
        "node_selector",
        "tolerations",
        "restart_policy",
    }
)


def _config_dict_to_k8s_fields(config: dict[str, Any]) -> dict[str, Any]:
    """Project a template-config dict onto K8sTemplate constructor kwargs.

    Normalises camelCase aliases to snake_case and keeps only keys that name a
    K8sTemplate field, so ``K8sTemplate.model_validate`` sees the operator's
    values (namespace, service_account, resource_*, restart_policy, ...) as
    typed fields rather than losing them in an untyped bag.
    """
    out: dict[str, Any] = {}
    for raw_key, value in config.items():
        key = _CONFIG_KEY_ALIASES.get(raw_key, raw_key)
        if key in _K8S_TEMPLATE_FIELDS and value is not None:
            out.setdefault(key, value)
    out.setdefault("template_id", config.get("template_id") or config.get("templateId") or "temp")
    out.setdefault("image_id", config.get("image_id") or config.get("imageId") or "")
    # Drop a non-positive max_instances so K8sTemplate construction does not
    # raise (the invalid value is already reported by _check_dict_only_rules);
    # dropping it lets the remaining typed rules still run.
    mi = out.get("max_instances")
    if mi is not None:
        try:
            if int(mi) <= 0:
                out.pop("max_instances", None)
        except (TypeError, ValueError):
            out.pop("max_instances", None)
    return out


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class K8sTemplateValidationResult:
    """Result of a single :meth:`K8sTemplateValidator.validate` call.

    Attributes:
        valid:   ``True`` when no errors were found.
        errors:  List of human-readable error messages.  Empty when valid.
        warnings: List of non-blocking advisory messages.
    """

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # pragma: no branch
        return self.valid


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class K8sTemplateValidator:
    """Registration-time validator for Kubernetes templates.

    Designed to be called synchronously at provider-registration time so
    that template configuration errors surface before the first acquire
    attempt reaches the Kubernetes API server.

    The validator accepts either a generic :class:`Template` or a fully-
    typed :class:`K8sTemplate`.  When a generic template is supplied it is
    up-cast lazily so the k8s-specific fields (namespace, service_account,
    resource_requests, …) are accessible.
    """

    def validate(self, template: Any) -> K8sTemplateValidationResult:
        """Validate *template* and return a :class:`K8sTemplateValidationResult`.

        Args:
            template: A :class:`~orb.domain.template.template_aggregate.Template`
                or :class:`~orb.providers.k8s.domain.template.k8s_template_aggregate.K8sTemplate`
                instance.

        Returns:
            A :class:`K8sTemplateValidationResult` — ``valid`` is ``False``
            and ``errors`` is non-empty when any rule fires.
        """
        # Upcast a generic Template to the typed K8sTemplate so the k8s-specific
        # rules below can read namespace / service_account / resource_* / etc.
        # from typed fields.  Callers (e.g. the infrastructure template adapter)
        # therefore need not import any k8s provider types — they pass a generic
        # Template with the k8s fields under provider_data["k8s"] and the upcast
        # here promotes them.  Already-K8sTemplate inputs pass through unchanged.
        # When a raw config dict is supplied, some values (e.g. max_instances=0)
        # would fail K8sTemplate construction and be lost in the coercion
        # fallback.  Check those directly on the dict first so they still surface.
        errors: list[str] = []
        if isinstance(template, dict):
            errors.extend(self._check_dict_only_rules(template))

        template = self._as_k8s_template(template)

        errors.extend(self._check_template_id(template))
        errors.extend(self._check_max_instances(template))
        errors.extend(self._check_provider_api(template))
        errors.extend(self._check_image_id(template))
        errors.extend(self._check_namespace(template))
        errors.extend(self._check_service_account(template))
        errors.extend(self._check_resource_quantities(template))
        errors.extend(self._check_tolerations(template))
        errors.extend(self._check_restart_policy_per_kind(template))

        return K8sTemplateValidationResult(valid=len(errors) == 0, errors=errors)

    @staticmethod
    def _check_dict_only_rules(config: dict[str, Any]) -> list[str]:
        """Rules checked on the raw config dict before typed coercion.

        Some values are rejected by ``K8sTemplate`` construction itself
        (e.g. ``max_instances <= 0``); coercing such a dict would raise and the
        value would be lost in the fallback.  Checking them here guarantees the
        error surfaces regardless of whether the typed model would accept it.
        """
        errors: list[str] = []
        raw_max = config.get("maxInstances")
        if raw_max is None:
            raw_max = config.get("maxNumber")
        if raw_max is None:
            raw_max = config.get("max_instances")
        if raw_max is not None:
            try:
                if int(raw_max) <= 0:
                    errors.append(f"max_instances must be a positive integer; got {raw_max!r}")
            except (TypeError, ValueError):
                errors.append(f"max_instances must be an integer; got {raw_max!r}")
        return errors

    @staticmethod
    def _as_k8s_template(template: Any) -> Any:
        """Return a K8sTemplate view of ``template``, best-effort.

        Accepts three input shapes so callers never need to import a k8s
        provider type to get k8s-specific validation:

        * an already-typed ``K8sTemplate`` — returned unchanged;
        * a plain ``dict`` (a template-config dict, snake_case or camelCase
          keys as the CLI / config surface supplies) — a ``K8sTemplate`` is
          constructed from it so the typed rules can inspect namespace /
          service_account / resource_* / restart_policy;
        * a generic ``Template`` — upcast to ``K8sTemplate``.

        Anything that cannot be coerced is returned as-is so the
        ``getattr``-based rule checks still run against whatever is present.
        """
        from orb.providers.k8s.domain.template.k8s_template_aggregate import (
            K8sTemplate,
            upcast_to_k8s_template,
        )

        if isinstance(template, K8sTemplate):
            return template
        if isinstance(template, dict):
            try:
                return K8sTemplate.model_validate(_config_dict_to_k8s_fields(template))
            except Exception:
                return template
        try:
            return upcast_to_k8s_template(template)
        except Exception:
            return template

    # ------------------------------------------------------------------
    # Rule implementations
    # ------------------------------------------------------------------

    def _check_template_id(self, template: Any) -> list[str]:
        """Rule 1: template_id must be set and non-empty."""
        tid = getattr(template, "template_id", None)
        if not tid or not str(tid).strip():
            return ["template_id is required and must be non-empty"]
        return []

    def _check_max_instances(self, template: Any) -> list[str]:
        """Rule 2: max_instances must be >= 1 when set."""
        max_inst = getattr(template, "max_instances", None)
        if max_inst is None:
            return []
        try:
            if int(max_inst) < 1:
                return [f"max_instances must be >= 1, got {max_inst!r}"]
        except (TypeError, ValueError):
            return [f"max_instances must be an integer >= 1, got {max_inst!r}"]
        return []

    def _check_provider_api(self, template: Any) -> list[str]:
        """Rule 3: provider_api must be one of the four supported APIs when set."""
        provider_api = getattr(template, "provider_api", None)
        if provider_api is None:
            return []
        if provider_api not in _SUPPORTED_PROVIDER_APIS:
            supported = sorted(_SUPPORTED_PROVIDER_APIS)
            return [f"provider_api {provider_api!r} is not supported; must be one of {supported}"]
        return []

    def _check_image_id(self, template: Any) -> list[str]:
        """Rule 4: image_id must be set and non-empty."""
        image_id = getattr(template, "image_id", None)
        if not image_id or not str(image_id).strip():
            return ["image_id (container image) is required and must be non-empty"]
        return []

    def _check_namespace(self, template: Any) -> list[str]:
        """Rule 5: namespace must match DNS-1123 label pattern when set."""
        namespace = getattr(template, "namespace", None)
        if namespace is None:
            return []
        ns_str = str(namespace)
        if len(ns_str) > 63 or not _DNS_1123_LABEL.match(ns_str):
            return [
                f"namespace {namespace!r} is not a valid DNS-1123 label "
                "(must be lowercase alphanumeric, may contain hyphens, "
                "must not start or end with a hyphen, max 63 characters)"
            ]
        return []

    def _check_service_account(self, template: Any) -> list[str]:
        """Rule 6: service_account must match DNS-1123 subdomain pattern when set.

        Kubernetes accepts serviceAccountName as a DNS-1123 subdomain (up to
        253 characters, dots allowed), not just a DNS-1123 label.  Dotted
        names like ``"my.sa"`` are therefore valid.
        """
        sa = getattr(template, "service_account", None)
        if sa is None:
            return []
        sa_str = str(sa)
        if len(sa_str) > _DNS_1123_SUBDOMAIN_MAX_LEN or not _DNS_1123_SUBDOMAIN.match(sa_str):
            return [
                f"service_account {sa!r} is not a valid DNS-1123 subdomain "
                "(must be lowercase alphanumeric, may contain hyphens and dots, "
                "must not start or end with a hyphen or dot, max 253 characters)"
            ]
        return []

    def _check_resource_quantities(self, template: Any) -> list[str]:
        """Rule 7: resource_requests / resource_limits quantity strings must be parseable."""
        errors: list[str] = []

        for attr in ("resource_requests", "resource_limits"):
            payload = getattr(template, attr, None)
            if payload is None:
                continue

            # Accept either a K8sResourceQuantities model (has to_resource_map)
            # or a plain dict of {resource: quantity} strings.
            if hasattr(payload, "to_resource_map"):
                resource_map: dict[str, str] = payload.to_resource_map()
            elif isinstance(payload, dict):
                resource_map = {str(k): str(v) for k, v in payload.items()}
            else:
                errors.append(
                    f"{attr}: unexpected payload type {type(payload).__name__!r}; "
                    "expected K8sResourceQuantities or dict"
                )
                continue

            for resource, quantity in resource_map.items():
                if not quantity:
                    continue
                if not _QUANTITY.match(str(quantity)):
                    errors.append(
                        f"{attr}: invalid quantity {quantity!r} for resource "
                        f"{resource!r} — must be a valid Kubernetes resource quantity "
                        "(e.g. '500m', '1Gi', '200M')"
                    )
        return errors

    def _check_tolerations(self, template: Any) -> list[str]:
        """Rule 8: tolerations entries must be parseable as K8sToleration."""
        raw_tolerations = getattr(template, "tolerations", None)
        if raw_tolerations is None:
            return []

        # When the toleration list is already typed (list[K8sToleration]) we
        # still walk the entries to verify each item has the expected shape.
        # Accept both typed model instances and raw dicts so this validator
        # works regardless of whether template was already up-cast.
        errors: list[str] = []
        entries: Any = raw_tolerations

        if not isinstance(entries, (list, tuple)):
            # Normalise a single dict / model entry.
            entries = [entries]

        for idx, entry in enumerate(entries):
            label = f"tolerations[{idx}]"
            if _is_k8s_toleration(entry):
                # Already validated by Pydantic during construction.
                continue
            if isinstance(entry, dict):
                err = _validate_toleration_dict(entry, label)
                if err:
                    errors.append(err)
            else:
                errors.append(
                    f"{label}: unexpected type {type(entry).__name__!r}; "
                    "expected a dict or K8sToleration"
                )

        return errors

    def _check_restart_policy_per_kind(self, template: Any) -> list[str]:
        """Rule 9: restart_policy must be compatible with provider_api when both are set.

        Kubernetes enforces per-kind constraints at admission time:
        * Job: "Always" is rejected — the spec requires "Never" or "OnFailure".
        * Deployment / StatefulSet: controller pods must restart; only
          "Always" (or unset, which defaults to "Always") is coherent.
          "Never" or "OnFailure" are rejected because the controller would
          create a pod that never recovers from container failure.
        """
        provider_api = getattr(template, "provider_api", None)
        restart_policy = getattr(template, "restart_policy", None)

        # Only enforced when both fields are present; absent values handled elsewhere.
        if not provider_api or not restart_policy:
            return []

        if provider_api == "Job" and restart_policy == "Always":
            return [
                "restart_policy 'Always' is not valid for provider_api 'Job'; "
                "use 'Never' or 'OnFailure' instead"
            ]

        if provider_api in ("Deployment", "StatefulSet") and restart_policy != "Always":
            return [
                f"restart_policy {restart_policy!r} is not valid for "
                f"provider_api {provider_api!r}; "
                "controller-managed pods must use 'Always' (or leave restart_policy unset)"
            ]

        return []


# ---------------------------------------------------------------------------
# Private toleration helpers
# ---------------------------------------------------------------------------


def _is_k8s_toleration(obj: Any) -> bool:
    """Return True when *obj* is an instance of K8sToleration."""
    # Avoid importing at module level to keep the validator lightweight.
    try:
        from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sToleration

        return isinstance(obj, K8sToleration)
    except ImportError:
        return False


def _validate_toleration_dict(entry: dict[str, Any], label: str) -> Optional[str]:
    """Validate a raw toleration dict by trying to parse it as K8sToleration.

    Returns an error string on failure or ``None`` on success.
    """
    try:
        from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sToleration

        K8sToleration.model_validate(entry)
        return None
    except Exception as exc:
        return f"{label}: invalid toleration entry — {exc}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "K8sTemplateValidationResult",
    "K8sTemplateValidator",
]
