"""Kubernetes-specific native-spec processing.

Mirrors :class:`orb.providers.aws.infrastructure.services.aws_native_spec_service.AWSNativeSpecService`
for the kubernetes provider.  The service is the integration point
between the generic application-layer :class:`NativeSpecService`
(Jinja rendering + flag plumbing) and the per-handler create paths in
:mod:`orb.providers.k8s.handlers`.

The escape hatch is opt-in via :attr:`K8sProviderConfig.native_spec_enabled`
(default False).  When enabled, the per-handler ``acquire_hosts`` paths
consult this service at submit time:

* If :attr:`K8sTemplate.native_spec` is set, the service renders it as a
  Jinja template against the standard context and deep-merges it onto the
  per-API default Jinja template (loaded from
  ``providers/k8s/specs/<api>/default.json``).  The merged dict is the
  body passed straight to the kubernetes SDK (e.g. ``create_namespaced_pod``).
* If :attr:`K8sTemplate.native_spec` is unset, the per-API default Jinja
  template is rendered and used directly.

When the flag is False, the service yields ``None`` and the caller falls
back to the typed spec builders in :mod:`orb.providers.k8s.utilities`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.infrastructure.di.injectable import injectable
from orb.infrastructure.utilities.common.deep_merge import deep_merge
from orb.providers.base.native_spec_protocol import NativeSpecServiceProtocol
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.domain.template.k8s_template_aggregate import (
    K8sTemplate,
    upcast_to_k8s_template,
)
from orb.providers.k8s.exceptions.k8s_exceptions import K8sError

# Supported per-API spec keys; each corresponds to a directory under
# ``providers/k8s/specs/`` containing a ``default.json`` Jinja template.
_SUPPORTED_API_TYPES: frozenset[str] = frozenset({"pod", "deployment", "statefulset", "job"})


def _validate_api_version_and_kind(spec: dict[str, Any], template_id: Any) -> None:
    """Raise :class:`K8sError` when ``spec`` is missing ``apiVersion`` or ``kind``.

    Both fields are mandatory on every Kubernetes object body.  Their absence
    means the native spec is misconfigured and would produce a confusing error
    from the API server rather than a clear operator-facing message.

    Args:
        spec: The fully rendered native-spec dict about to be sent to the SDK.
        template_id: Used in the error message so the operator can locate the
            offending template.

    Raises:
        K8sError: when ``apiVersion`` or ``kind`` is missing or empty.
    """
    missing = [field for field in ("apiVersion", "kind") if not spec.get(field)]
    if missing:
        raise K8sError(
            f"native_spec rendered dict for template {template_id!r} is missing "
            f"required field(s): {missing!r}.  Ensure the native_spec (or the "
            f"default template it merges onto) sets apiVersion and kind."
        )


@injectable
class K8sNativeSpecService:
    """Kubernetes-specific native-spec processing.

    The class is deliberately thin — Jinja rendering and the enable flag
    live on the shared application-layer :class:`NativeSpecService`
    (passed in via ``native_spec_service``); this class adds the k8s
    template resolution, the per-API default Jinja template loader, and
    the deep-merge with the operator-supplied native spec.
    """

    def __init__(
        self,
        native_spec_service: NativeSpecServiceProtocol,
        config_port: ConfigurationPort,
        k8s_config: Optional[K8sProviderConfig] = None,
    ) -> None:
        self.native_spec_service = native_spec_service
        self.config_port = config_port
        self.spec_renderer = native_spec_service.spec_renderer
        self._k8s_config = k8s_config

    # ------------------------------------------------------------------
    # Enable-flag plumbing
    # ------------------------------------------------------------------

    def is_native_spec_enabled(self) -> bool:
        """Return True only when both the provider config and the generic
        application flag agree the escape hatch is active.

        The provider-specific flag (:attr:`K8sProviderConfig.native_spec_enabled`)
        is the operator-facing kill switch — if the kubernetes provider
        instance does not opt in, the hatch stays closed regardless of
        the generic application setting.  The application setting acts
        as the global override that operators can flip without rebuilding
        the provider config.
        """
        if self._k8s_config is not None and not self._k8s_config.native_spec_enabled:
            return False
        return self.native_spec_service.is_native_spec_enabled()

    # ------------------------------------------------------------------
    # Spec resolution
    # ------------------------------------------------------------------

    def render_spec(self, spec: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Render ``spec`` with Jinja variables from ``context``.

        Thin pass-through to the generic spec renderer so callers do not
        need to reach across into the application layer.
        """
        return self.native_spec_service.render_spec(spec, context)

    def render_default_spec(self, api_type: str, context: dict[str, Any]) -> dict[str, Any]:
        """Render the per-API default Jinja template.

        Args:
            api_type: One of ``"pod"`` / ``"deployment"`` / ``"statefulset"``
                / ``"job"`` — corresponds to the directory layout under
                ``providers/k8s/specs/``.
            context: Template context variables (image, namespace, labels,
                resource quantities, ...).

        Returns:
            The rendered default spec as a dict ready to feed straight to
            the kubernetes SDK.

        Raises:
            ValueError: when ``api_type`` is not a known kubernetes API.
            Exception: re-raised when the underlying renderer fails — the
                error is logged via the renderer's logger first.
        """
        key = api_type.lower()
        if key not in _SUPPORTED_API_TYPES:
            raise ValueError(
                f"Unsupported kubernetes native-spec api_type: {api_type!r} "
                f"(supported: {sorted(_SUPPORTED_API_TYPES)})"
            )

        spec_file_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "specs",
            key,
            "default.json",
        )

        try:
            return self.spec_renderer.render_spec_from_file(  # type: ignore[attr-defined]
                spec_file_path, context
            )
        except Exception as e:
            self.native_spec_service.logger.error(
                "Failed to render default k8s spec for %s: %s", key, e
            )
            raise

    # ------------------------------------------------------------------
    # Per-API entry points (called from the handlers at acquire time)
    # ------------------------------------------------------------------

    def process_pod_spec(
        self, template: Any, request: Request, *, namespace: str
    ) -> Optional[dict[str, Any]]:
        """Resolve the ``V1Pod`` body for the Pod handler when enabled.

        Returns ``None`` when the escape hatch is disabled — callers fall
        back to :func:`orb.providers.k8s.utilities.pod_spec.build_pod_spec`.
        """
        return self._process(template, request, api_type="pod", namespace=namespace)

    def process_deployment_spec(
        self, template: Any, request: Request, *, namespace: str
    ) -> Optional[dict[str, Any]]:
        """Resolve the ``V1Deployment`` body when enabled, else ``None``."""
        return self._process(template, request, api_type="deployment", namespace=namespace)

    def process_statefulset_spec(
        self, template: Any, request: Request, *, namespace: str
    ) -> Optional[dict[str, Any]]:
        """Resolve the ``V1StatefulSet`` body when enabled, else ``None``."""
        return self._process(template, request, api_type="statefulset", namespace=namespace)

    def process_job_spec(
        self, template: Any, request: Request, *, namespace: str
    ) -> Optional[dict[str, Any]]:
        """Resolve the ``V1Job`` body when enabled, else ``None``."""
        return self._process(template, request, api_type="job", namespace=namespace)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_resource_name(self, request: Request, api_type: str) -> str:
        """Compute the configured resource name for the native-spec path.

        Both the typed-builder path (handlers calling make_*_name) and the
        native-spec path (Jinja templates using ``{{ resource_name }}``) must
        produce the **same** name so that release/status lookups via
        ``provider_data["<kind>_name"]`` work regardless of which path was used.

        Delegates to the same make_*_name utilities the typed-builder handlers
        use, passing the naming config from the provider config when available.
        """
        from orb.providers.k8s.utilities.deployment_spec import make_deployment_name
        from orb.providers.k8s.utilities.job_spec import make_job_name
        from orb.providers.k8s.utilities.pod_spec import make_pod_name
        from orb.providers.k8s.utilities.statefulset_spec import make_statefulset_name

        naming = getattr(self._k8s_config, "naming", None) if self._k8s_config is not None else None
        rid = str(request.request_id)

        key = api_type.lower()
        if key == "pod":
            # For the native-spec path a single Pod-0 name is produced; the
            # per-pod loop in K8sPodHandler overrides with the per-seq name.
            return make_pod_name(rid, 0, naming=naming)
        if key == "deployment":
            return make_deployment_name(rid, naming=naming)
        if key == "statefulset":
            return make_statefulset_name(rid, naming=naming)
        if key == "job":
            return make_job_name(rid, naming=naming)
        # Fallback for unknown api_types: use the request_id prefix directly.
        safe = rid.replace("-", "")
        return f"orb-{safe[:8]}"

    def _resolve_native_spec_path(self, spec_path: str) -> str:
        """Resolve a native_spec_path to an absolute filesystem path.

        Resolution order:
        1. Absolute paths are used as-is.
        2. Relative paths are resolved against
           ``K8sProviderConfig.native_spec_base_path`` when set.
        3. Otherwise resolved against the current working directory.

        After resolution the path is checked for existence (raises
        :class:`K8sError` when missing) and, when a base is configured,
        path traversal outside the base is rejected.

        Args:
            spec_path: The ``native_spec_path`` value from the template.

        Returns:
            Absolute path string suitable for :meth:`render_spec_from_file`.

        Raises:
            K8sError: when the resolved path does not exist or when it
                traverses outside the configured ``native_spec_base_path``.
        """
        raw = Path(spec_path)
        base_str = getattr(self._k8s_config, "native_spec_base_path", None)

        if raw.is_absolute():
            resolved = raw.resolve()
        elif base_str is not None:
            resolved = (Path(base_str) / raw).resolve()
        else:
            resolved = (Path.cwd() / raw).resolve()

        # Guard against path traversal when a base directory is configured.
        if base_str is not None:
            base_resolved = Path(base_str).resolve()
            try:
                resolved.relative_to(base_resolved)
            except ValueError:
                raise K8sError(
                    f"native_spec_path {spec_path!r} resolves to {resolved!s} which is "
                    f"outside the configured native_spec_base_path {base_str!r}.  "
                    "Path traversal is not permitted."
                )

        if not resolved.exists():
            raise K8sError(
                f"native_spec_path {spec_path!r} resolved to {resolved!s} but the file "
                "does not exist.  Ensure the path is correct and the file is readable."
            )

        return str(resolved)

    def _process(
        self,
        template: Any,
        request: Request,
        *,
        api_type: str,
        namespace: str,
    ) -> Optional[dict[str, Any]]:
        """Resolve the rendered native-spec body for ``api_type``.

        When the escape hatch is disabled, returns ``None`` so the caller
        falls back to the typed builder path.  A warning is logged when
        ``native_spec`` or ``native_spec_path`` is set on the template but
        the flag is disabled, so operators are not silently surprised by the
        bypass.

        When enabled:

        * If ``K8sTemplate.native_spec`` is set, render it and deep-merge it
          onto the per-API default Jinja template (default first, then
          operator override wins on leaf collisions).  When ``pod_spec_override``
          is also set, it is ignored with a warning — ``native_spec`` is a
          full-replacement intent and layering a partial override on top of it
          would produce undefined behaviour.
        * If ``K8sTemplate.native_spec_path`` is set (and ``native_spec`` is
          not), the file at the resolved path is Jinja-rendered and used as the
          override dict — the same deep-merge-onto-default pipeline applies.
          When both ``native_spec`` and ``native_spec_path`` are set, inline
          ``native_spec`` wins and a warning is logged.
        * Otherwise, render the default Jinja template and return it directly.

        The rendered dict is validated to carry ``apiVersion`` and ``kind``
        before it is returned — both fields are required by the Kubernetes API
        server and their absence always indicates a misconfigured native spec.

        Raises:
            K8sError: when the final rendered dict is missing ``apiVersion``
                or ``kind``, when the spec file is not found, or when path
                traversal outside the base directory is attempted.
        """
        k8s_template = upcast_to_k8s_template(template)

        if not self.is_native_spec_enabled():
            if k8s_template.native_spec or k8s_template.native_spec_path:
                self.native_spec_service.logger.warning(
                    "native_spec is set on template %r but native_spec_enabled=False; "
                    "falling back to the typed K8sTemplate path",
                    k8s_template.template_id,
                )
            return None

        context = self._build_k8s_context(
            k8s_template, request, namespace=namespace, api_type=api_type
        )

        # Always render the default first so operators can submit a
        # partial override (e.g. only ``spec.containers[0].resources``).
        default_spec = self.render_default_spec(api_type, context)

        if k8s_template.native_spec:
            if k8s_template.native_spec_path:
                self.native_spec_service.logger.warning(
                    "Both native_spec and native_spec_path are set on template %r; "
                    "inline native_spec takes precedence and native_spec_path will be ignored",
                    k8s_template.template_id,
                )
            if k8s_template.pod_spec_override:
                self.native_spec_service.logger.warning(
                    "Both native_spec and pod_spec_override are set on template %r; "
                    "native_spec takes precedence and pod_spec_override will be ignored",
                    k8s_template.template_id,
                )
            rendered_override = self.render_spec(k8s_template.native_spec, context)
            result = deep_merge(default_spec, rendered_override)
            _validate_api_version_and_kind(result, k8s_template.template_id)
            return result

        if k8s_template.native_spec_path:
            resolved_path = self._resolve_native_spec_path(k8s_template.native_spec_path)
            rendered_override = self.spec_renderer.render_spec_from_file(  # type: ignore[attr-defined]
                resolved_path, context
            )
            result = deep_merge(default_spec, rendered_override)
            _validate_api_version_and_kind(result, k8s_template.template_id)
            return result

        return default_spec

    def _build_k8s_context(
        self,
        k8s_template: K8sTemplate,
        request: Request,
        *,
        namespace: str,
        api_type: str = "pod",
    ) -> dict[str, Any]:
        """Build the kubernetes-specific Jinja context.

        Mirrors :meth:`AWSNativeSpecService._build_aws_context` in role:
        flat keys covering image, sizing, labels, namespace, identity,
        and per-API container fields the default templates reference.
        """
        package_info = self.config_port.get_package_info()

        image = k8s_template.image_id or ""
        replicas = max(int(request.requested_count), 1)

        resource_requests = k8s_template.resolve_resource_requests_map() or {}
        resource_limits = k8s_template.resolve_resource_limits_map() or {}

        operator_labels = k8s_template.resolve_pod_labels()

        # Standard ORB-system labels — match the typed-builder output so
        # native specs that omit labels still carry the controller-side
        # invariants (managed sentinel, request-id selector, ...).
        label_prefix = "orb.io"
        if self._k8s_config is not None:
            label_prefix = self._k8s_config.label_prefix

        system_labels: dict[str, str] = {
            f"{label_prefix}/managed": "true",
            f"{label_prefix}/request-id": str(request.request_id),
            f"{label_prefix}/provider-api": str(getattr(request, "provider_api", "")),
            f"{label_prefix}/template-id": str(request.template_id),
        }

        merged_labels: dict[str, str] = dict(operator_labels)
        merged_labels.update(system_labels)

        env_list = k8s_template.resolve_env_api_list() or []
        volume_mounts = list(k8s_template.volume_mounts or [])
        volumes_list = k8s_template.resolve_volumes_api_list() or []
        tolerations_list = k8s_template.resolve_tolerations_api_list() or []

        # Compute the configured resource name once so both the metadata.name
        # field and any template references to {{ resource_name }} are consistent
        # with the typed-builder path.
        resource_name = self._resolve_resource_name(request, api_type)

        return {
            "resource_name": resource_name,
            "request_id": str(request.request_id),
            "requested_count": replicas,
            "replicas": replicas,
            "template_id": k8s_template.template_id,
            "image": image,
            "image_id": image,
            "namespace": namespace,
            "provider_api": str(getattr(request, "provider_api", "")),
            "label_prefix": label_prefix,
            "labels": merged_labels,
            "annotations": dict(k8s_template.annotations or {}),
            "service_account": k8s_template.service_account,
            "runtime_class": k8s_template.runtime_class,
            "node_selector": dict(k8s_template.node_selector or {}),
            "image_pull_secret": k8s_template.image_pull_secret,
            "resource_requests": resource_requests,
            "resource_limits": resource_limits,
            "command": list(k8s_template.command or []),
            "args": list(k8s_template.args or []),
            "env": env_list,
            "volume_mounts": volume_mounts,
            "volumes": volumes_list,
            "tolerations": tolerations_list,
            "has_command": bool(k8s_template.command),
            "has_args": bool(k8s_template.args),
            "has_env": bool(env_list),
            "has_volume_mounts": bool(volume_mounts),
            "has_volumes": bool(volumes_list),
            "has_tolerations": bool(tolerations_list),
            "has_resource_requests": bool(resource_requests),
            "has_resource_limits": bool(resource_limits),
            "has_node_selector": bool(k8s_template.node_selector),
            "has_service_account": bool(k8s_template.service_account),
            "has_runtime_class": bool(k8s_template.runtime_class),
            "has_image_pull_secret": bool(k8s_template.image_pull_secret),
            "has_annotations": bool(k8s_template.annotations),
            "package_name": package_info.get("name", "open-resource-broker"),
            "package_version": package_info.get("version", "unknown"),
        }


__all__ = ["K8sNativeSpecService", "_validate_api_version_and_kind"]
