"""Kubernetes template validator — registration-time structural checks.

Validates :class:`~orb.domain.template.template_aggregate.Template` (and its
:class:`~orb.providers.k8s.domain.template.k8s_template.K8sTemplate`
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
6. ``service_account`` — when set, same DNS-1123 label constraint as
   ``namespace``.
7. ``resource_requests`` / ``resource_limits`` — when set, every quantity
   string in the emitted resource map must parse cleanly via
   :func:`~orb.providers.k8s.utilities.quantity_parser.parse_cpu_quantity`
   (for CPU entries) or
   :func:`~orb.providers.k8s.utilities.quantity_parser.parse_memory_quantity`
   (for all other entries).
8. ``tolerations`` — when set, each entry must be parseable as a
   :class:`~orb.providers.k8s.domain.template.k8s_template.K8sToleration`
   (Pydantic-backed, tolerates dict or model input).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Internal constants — mirrors the consts in template_adapter.py but kept
# local so this module has no hard import dependency on the adapter.
# ---------------------------------------------------------------------------

#: kubernetes resource-API types recognised by the v1 provider.
_SUPPORTED_PROVIDER_APIS: frozenset[str] = frozenset({"Pod", "Deployment", "StatefulSet", "Job"})

#: DNS-1123 label pattern (namespace / service-account names).
#:
#: Rules:
#: * must start and end with ``[a-z0-9]``
#: * interior characters may be ``[a-z0-9-]``
#: * maximum length is 63 characters (Kubernetes restriction)
_DNS_1123_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")

#: Kubernetes resource-quantity pattern (CPU / memory / storage).
#: Mirrors the regex in :mod:`template_adapter`.
_QUANTITY = re.compile(
    r"^[+-]?(\d+(\.\d+)?|\.\d+)"  # numeric magnitude
    r"([eE][+-]?\d+)?"  # optional exponent
    r"([numµ]|[kKMGTPE]i?)?$"  # SI / binary suffix
)


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
                or :class:`~orb.providers.k8s.domain.template.k8s_template.K8sTemplate`
                instance.

        Returns:
            A :class:`K8sTemplateValidationResult` — ``valid`` is ``False``
            and ``errors`` is non-empty when any rule fires.
        """
        errors: list[str] = []

        errors.extend(self._check_template_id(template))
        errors.extend(self._check_max_instances(template))
        errors.extend(self._check_provider_api(template))
        errors.extend(self._check_image_id(template))
        errors.extend(self._check_namespace(template))
        errors.extend(self._check_service_account(template))
        errors.extend(self._check_resource_quantities(template))
        errors.extend(self._check_tolerations(template))

        return K8sTemplateValidationResult(valid=len(errors) == 0, errors=errors)

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
        if not _DNS_1123_LABEL.match(ns_str):
            return [
                f"namespace {namespace!r} is not a valid DNS-1123 label "
                "(must be lowercase alphanumeric, may contain hyphens, "
                "must not start or end with a hyphen, max 63 characters)"
            ]
        return []

    def _check_service_account(self, template: Any) -> list[str]:
        """Rule 6: service_account must match DNS-1123 label pattern when set."""
        sa = getattr(template, "service_account", None)
        if sa is None:
            return []
        sa_str = str(sa)
        if not _DNS_1123_LABEL.match(sa_str):
            return [
                f"service_account {sa!r} is not a valid DNS-1123 label "
                "(must be lowercase alphanumeric, may contain hyphens, "
                "must not start or end with a hyphen, max 63 characters)"
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


# ---------------------------------------------------------------------------
# Private toleration helpers
# ---------------------------------------------------------------------------


def _is_k8s_toleration(obj: Any) -> bool:
    """Return True when *obj* is an instance of K8sToleration."""
    # Avoid importing at module level to keep the validator lightweight.
    try:
        from orb.providers.k8s.domain.template.k8s_template import K8sToleration  # noqa: PLC0415

        return isinstance(obj, K8sToleration)
    except ImportError:
        return False


def _validate_toleration_dict(entry: dict[str, Any], label: str) -> Optional[str]:
    """Validate a raw toleration dict by trying to parse it as K8sToleration.

    Returns an error string on failure or ``None`` on success.
    """
    try:
        from orb.providers.k8s.domain.template.k8s_template import K8sToleration  # noqa: PLC0415

        K8sToleration.model_validate(entry)
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{label}: invalid toleration entry — {exc}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "K8sTemplateValidationResult",
    "K8sTemplateValidator",
]
