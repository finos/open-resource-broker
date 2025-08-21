"""Jinja2 implementation of spec rendering."""

from typing import Any, Dict

from jinja2 import BaseLoader, Environment, select_autoescape

from domain.base.dependency_injection import injectable
from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.spec_rendering_port import SpecRenderingPort


@injectable
class JinjaSpecRenderer(SpecRenderingPort):
    """Jinja2 implementation of spec rendering."""

    def __init__(self, logger: LoggingPort):
        self.logger = logger
        self.jinja_env = Environment(
            loader=BaseLoader(), autoescape=select_autoescape(["json", "yaml", "yml"])
        )

    def render_spec_from_file(self, file_path: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """Render specification from file with Jinja2 templating support.
        
        Args:
            file_path: Path to the specification file
            context: Template context variables
            
        Returns:
            Rendered specification as dictionary
        """
        try:
            # Read file content
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Always process through Jinja2 - handles static content automatically
            template = self.jinja_env.from_string(content)
            rendered_content = template.render(**context)
            
            # Parse rendered JSON
            import json
            return json.loads(rendered_content)
                
        except Exception as e:
            self.logger.error(f"Failed to render spec from file {file_path}: {e}")
            raise

    def render_spec(self, spec: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Render Jinja2 templates in spec values."""
        return self._render_recursive(spec, context)

    def _render_recursive(self, obj: Any, context: Dict[str, Any]) -> Any:
        """Recursively render templates in nested structures."""
        if isinstance(obj, dict):
            return {k: self._render_recursive(v, context) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._render_recursive(item, context) for item in obj]
        elif isinstance(obj, str) and "{{" in obj:
            template = self.jinja_env.from_string(obj)
            return template.render(**context)
        return obj
