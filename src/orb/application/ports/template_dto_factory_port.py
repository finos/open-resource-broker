"""Port for converting domain template aggregates to TemplateDTOs.

The concrete implementation (``TemplateDTOFactory``) lives in
``orb.infrastructure.template.factories`` because it depends on
``TemplateExtensionRegistry``.  Application-layer handlers depend only on
this port.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from orb.application.dto.template import TemplateDTO


class TemplateDTOFactoryPort(ABC):
    """Port for converting domain Template aggregates into TemplateDTOs."""

    @abstractmethod
    def from_domain(self, template: Any) -> TemplateDTO:
        """Convert a domain template aggregate to a TemplateDTO."""
