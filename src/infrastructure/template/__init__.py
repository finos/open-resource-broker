"""Template infrastructure components."""

from .configuration_manager import TemplateConfigurationManager
from .template_cache_service import (
    NoOpTemplateCacheService,
    TemplateCacheService,
    create_template_cache_service,
)
from .template_repository_impl import TemplateRepositoryImpl

__all__: list[str] = [
    # Core template system
    "TemplateConfigurationManager",
    # Repository implementation
    "TemplateRepositoryImpl",
    # Caching components
    "TemplateCacheService",
    "NoOpTemplateCacheService",
    "create_template_cache_service",
]
