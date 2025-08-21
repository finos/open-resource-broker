"""Generic native spec processing service."""

from typing import Any, Dict

from domain.base.dependency_injection import injectable
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.spec_rendering_port import SpecRenderingPort


@injectable
class NativeSpecService:
    """Generic native spec processing service - provider agnostic."""

    def __init__(self, config_port: ConfigurationPort, spec_renderer: SpecRenderingPort):
        self.config_port = config_port
        self.spec_renderer = spec_renderer

    def is_native_spec_enabled(self) -> bool:
        """Check if native specs are enabled."""
        return self.config_port.get_native_spec_config()["enabled"]

    def render_spec(self, spec: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Render spec with context - provider agnostic."""
        return self.spec_renderer.render_spec(spec, context)
