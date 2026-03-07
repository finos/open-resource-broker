"""Base class for AWS handler config builders.

Provides the shared native-spec processing scaffold used by all config builders:
  1. Prepare template context
  2. Call process_provider_api_spec_with_merge
  3. If a spec is returned, inject launch template refs and return it
  4. Otherwise fall back to render_default_spec
  5. If no native spec service, fall back to legacy construction

Subclasses implement the three variation points:
  - _api_key()              -- the provider API key string (e.g. "asg", "ec2fleet")
  - _prepare_template_context() -- build the context dict for the native spec renderer
  - _inject_launch_template()   -- patch LT id/version into the rendered spec
  - _build_legacy()             -- legacy config construction when no native spec service
"""

from abc import ABC, abstractmethod
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.request.aggregate import Request
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate


class BaseConfigBuilder(ABC):
    """Shared native-spec processing scaffold for all AWS config builders."""

    def __init__(
        self,
        native_spec_service: Optional[Any],
        config_port: Optional[ConfigurationPort],
        logger: LoggingPort,
    ) -> None:
        self._native_spec_service = native_spec_service
        self._config_port = config_port
        self._logger = logger

    # ------------------------------------------------------------------
    # Shared scaffold
    # ------------------------------------------------------------------

    def _process_native_spec(
        self,
        template: AWSTemplate,
        request: Request,
        lt_id: str,
        lt_version: str,
        extra_context: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Run the native-spec processing path and return the result, or None.

        Builds the template context, calls process_provider_api_spec_with_merge,
        injects launch template references into the result, and logs success.
        Returns None when the native spec service returns nothing (caller should
        fall through to render_default_spec or _build_legacy).
        """
        context = self._prepare_template_context(template, request)
        context["launch_template_id"] = lt_id
        context["launch_template_version"] = lt_version
        if extra_context:
            context.update(extra_context)

        native_spec = self._native_spec_service.process_provider_api_spec_with_merge(  # type: ignore[union-attr]
            template, request, self._api_key(), context
        )
        if native_spec:
            self._inject_launch_template(native_spec, template, lt_id, lt_version)
            self._logger.info(
                "Using native provider API spec with merge for %s template %s",
                self._api_key(),
                template.template_id,
            )
            return native_spec

        return None

    def _render_default(
        self,
        template: AWSTemplate,
        request: Request,
        lt_id: str,
        lt_version: str,
        extra_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Render the default native spec for this API key."""
        context = self._prepare_template_context(template, request)
        context["launch_template_id"] = lt_id
        context["launch_template_version"] = lt_version
        if extra_context:
            context.update(extra_context)
        return self._native_spec_service.render_default_spec(self._api_key(), context)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Subclass hooks
    # ------------------------------------------------------------------

    @abstractmethod
    def _api_key(self) -> str:
        """Return the provider API key used for native spec lookup (e.g. 'asg')."""

    @abstractmethod
    def _prepare_template_context(self, template: AWSTemplate, request: Request) -> dict[str, Any]:
        """Build the context dict passed to the native spec renderer."""

    @abstractmethod
    def _inject_launch_template(
        self,
        native_spec: dict[str, Any],
        template: AWSTemplate,
        lt_id: str,
        lt_version: str,
    ) -> None:
        """Patch launch template id/version into the rendered native spec in-place."""

    def _build_tag_context(
        self,
        request_id: str,
        template_id: str,
        provider_api: str,
        template_tags: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Build base_tags and custom_tags for Jinja template rendering.

        Returns a dict with keys: base_tags, custom_tags, has_custom_tags.
        Tags are in lowercase-key format ({"key": ..., "value": ...}) as
        expected by the Jinja default specs.
        """
        from orb.providers.aws.infrastructure.tags import SYSTEM_TAG_PREFIX, build_system_tags

        system_tags = build_system_tags(
            request_id=request_id,
            template_id=template_id,
            provider_api=provider_api,
        )
        base_tags = [{"key": t["Key"], "value": t["Value"]} for t in system_tags]

        custom_tags: list[dict[str, str]] = []
        if template_tags:
            custom_tags = [
                {"key": k, "value": str(v)}
                for k, v in template_tags.items()
                if not k.startswith(SYSTEM_TAG_PREFIX)
            ]

        return {
            "base_tags": base_tags,
            "custom_tags": custom_tags,
            "has_custom_tags": bool(custom_tags),
        }
