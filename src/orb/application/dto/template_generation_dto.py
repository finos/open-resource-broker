"""Template Generation DTOs - Data Transfer Objects for Template Generation Service."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class TemplateGenerationRequest:
    """Request object for template generation."""

    specific_provider: Optional[str] = None
    all_providers: bool = False
    provider_api: Optional[str] = None
    provider_specific: bool = False
    provider_type_filter: Optional[str] = None
    force_overwrite: bool = False


@dataclass
class ProviderTemplateResult:
    """Result for template generation for a single provider."""

    provider: str
    filename: str
    templates_count: int
    path: str
    status: str  # "created", "skipped", "error"
    reason: Optional[str] = None


@dataclass
class TemplateGenerationResult:
    """Overall result for template generation operation."""

    status: str  # "success", "error"
    message: str
    providers: List[ProviderTemplateResult]
    total_templates: int
    created_count: int
    skipped_count: int
