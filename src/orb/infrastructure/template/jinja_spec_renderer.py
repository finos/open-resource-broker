"""Jinja2 implementation of spec rendering."""

from typing import Any

from jinja2 import BaseLoader, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

from orb.domain.base.ports.logging_port import LoggingPort
from orb.domain.base.ports.spec_rendering_port import SpecRenderingPort
from orb.infrastructure.di.injectable import injectable


@injectable
class JinjaSpecRenderer(SpecRenderingPort):
    """Jinja2 implementation of spec rendering.

    Uses a sandboxed Jinja2 environment to prevent server-side template injection
    (SSTI) attacks.  Operators supplying native_spec templates cannot use class
    traversal chains (e.g. ``{{ ''.__class__.__mro__[1].__subclasses__() }}``)
    to escape the render context or execute arbitrary code.
    """

    def __init__(self, logger: LoggingPort):
        self.logger = logger
        self.jinja_env = SandboxedEnvironment(
            loader=BaseLoader(), autoescape=select_autoescape(["json", "yaml", "yml"])
        )

    def render_spec_from_file(self, file_path: str, context: dict[str, Any]) -> dict[str, Any]:
        """Render specification from file with Jinja2 templating support.

        Supports both JSON and YAML manifest files.  The file suffix determines
        the parser: ``.yaml`` / ``.yml`` files are parsed with
        ``yaml.safe_load``; all other extensions (including ``.json``) fall
        back to ``json.loads``.  Both formats pass through Jinja2 rendering
        first so template variables (e.g. ``{{ request_id }}``) work
        regardless of file format.

        Args:
            file_path: Path to the specification file.
            context: Template context variables (Jinja2 variables).

        Returns:
            Rendered specification as dictionary.
        """
        try:
            # Read file content
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            # Always process through Jinja2 — handles static content automatically
            template = self.jinja_env.from_string(content)
            rendered_content = template.render(**context)

            # Branch on file suffix to select the correct parser.
            suffix = file_path.lower().rsplit(".", 1)[-1] if "." in file_path else ""
            if suffix in ("yaml", "yml"):
                import yaml

                return yaml.safe_load(rendered_content)  # type: ignore[no-any-return]
            else:
                import json

                return json.loads(rendered_content)

        except Exception as e:
            rendered_content_safe = (
                rendered_content if "rendered_content" in dir() else "<unrendered>"
            )  # type: ignore[possibly-undefined]
            self.logger.error(
                f"Failed to render spec from file {file_path}: {e} context: {context} \n file: {rendered_content_safe} "
            )
            raise

    def render_spec(self, spec: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Render Jinja2 templates in spec values."""
        return self._render_recursive(spec, context)

    def _render_recursive(self, obj: Any, context: dict[str, Any]) -> Any:
        """Recursively render templates in nested structures."""
        if isinstance(obj, dict):
            return {k: self._render_recursive(v, context) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._render_recursive(item, context) for item in obj]
        elif isinstance(obj, str) and "{{" in obj:
            template = self.jinja_env.from_string(obj)
            return template.render(**context)
        return obj
