"""Template contracts for domain layer."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class TemplateContract:
    """
    Domain-level template contract.

    This represents the template data structure that the domain layer
    expects, without depending on infrastructure DTOs.
    """

    template_id: str
    name: str
    provider_api: str
    configuration: Dict[str, Any]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    version: Optional[str] = None
    tags: Optional[Dict[str, str]] = None

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.template_id:
            raise ValueError("template_id is required")
        if not self.name:
            raise ValueError("name is required")
        if not self.provider_api:
            raise ValueError("provider_api is required")


@dataclass
class TemplateValidationResult:
    """Template validation result contract."""

    is_valid: bool
    errors: List[str]
    warnings: List[str]
    template_id: str

    def has_errors(self) -> bool:
        """Check if validation has errors."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if validation has warnings."""
        return len(self.warnings) > 0


@dataclass
class TemplateMetadata:
    """Template metadata contract."""

    template_id: str
    provider_apis: List[str]
    last_modified: datetime
    version: str
    is_active: bool
