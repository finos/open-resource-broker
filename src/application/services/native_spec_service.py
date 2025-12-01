"""Generic native spec processing service."""

from typing import Any

from domain.base.dependency_injection import injectable
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.spec_rendering_port import SpecRenderingPort


@injectable
class NativeSpecService:
    """Generic native spec processing service - provider agnostic."""

    def __init__(
        self, config_port: ConfigurationPort, spec_renderer: SpecRenderingPort, logger: LoggingPort
    ):
        self.config_port = config_port
        self.spec_renderer = spec_renderer
        self.logger = logger

    def is_native_spec_enabled(self) -> bool:
        """Check if native specs are enabled."""
        try:
            config = self.config_port.get_native_spec_config() or {}
            enabled = config.get("enabled")
            return bool(enabled) if isinstance(enabled, bool) else False
        except Exception:
            # Surface unexpected config errors to callers
            raise

    def render_spec(self, spec: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Render spec with context - provider agnostic."""
        if spec is None or context is None:
            raise TypeError("spec and context must not be None")
        return self.spec_renderer.render_spec(spec, context)
