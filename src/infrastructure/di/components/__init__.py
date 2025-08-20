"""
DI Container components package.

This package provides modular dependency injection components:
- ServiceRegistry: Service registration management
- CQRSHandlerRegistry: CQRS handler registration
- DependencyResolver: Dependency resolution engine
"""

from .cqrs_registry import CQRSHandlerRegistry
from .dependency_resolver import DependencyResolver
from .service_registry import ServiceRegistry

__all__: list[str] = ["ServiceRegistry", "CQRSHandlerRegistry", "DependencyResolver"]
