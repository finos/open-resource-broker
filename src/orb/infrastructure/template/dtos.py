"""Infrastructure template DTOs — backward-compat shim.

``TemplateDTO`` has been moved to ``orb.application.dto.template``.
This module re-exports it so that existing infrastructure callers
(configuration_manager, template_repository_impl, provider adapters, etc.)
continue to work without modification.

The ``from_domain`` conversion that requires ``TemplateExtensionRegistry``
now lives in ``orb.infrastructure.template.factories.TemplateDTOFactory``.
Application-layer callers that previously called ``TemplateDTO.from_domain``
should inject and call ``TemplateDTOFactory.from_domain`` instead.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# Re-export from canonical location so all existing imports keep working.
from orb.application.dto.template import TemplateDTO

__all__ = ["TemplateDTO", "TemplateValidationResultDTO", "TemplateCacheEntryDTO"]


@dataclass
class TemplateValidationResultDTO:
    """Template validation result DTO."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]
    template_id: str

    def has_errors(self) -> bool:
        """Check if validation has errors."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if validation has warnings."""
        return len(self.warnings) > 0


@dataclass
class TemplateCacheEntryDTO:
    """Template cache entry DTO."""

    template: TemplateDTO
    cached_at: datetime
    expires_at: Optional[datetime] = None
    access_count: int = 0

    def is_expired(self) -> bool:
        """Check if cache entry is expired."""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at
