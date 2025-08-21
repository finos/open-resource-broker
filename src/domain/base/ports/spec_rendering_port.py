"""Port for spec text rendering services."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class SpecRenderingPort(ABC):
    """Port for spec text rendering services."""

    @abstractmethod
    def render_spec(self, spec: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """Render spec with template variables."""
