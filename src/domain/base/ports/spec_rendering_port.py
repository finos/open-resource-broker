"""Port for spec text rendering services."""

from abc import ABC, abstractmethod
from typing import Any


class SpecRenderingPort(ABC):
    """Port for spec text rendering services."""

    @abstractmethod
    def render_spec(self, spec: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        """Render spec with template variables."""
