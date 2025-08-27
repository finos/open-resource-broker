"""Template Resolver Port - Interface for template parameter resolution."""

from abc import ABC, abstractmethod
from typing import Optional


class TemplateResolverPort(ABC):
    """
    Port interface for template parameter resolution.

    This port defines the contract for resolving template parameters
    such as AMI IDs from SSM parameters, without coupling to specific
    AWS implementations.

    Clean Architecture Principles:
    - Domain layer defines the interface (port)
    - Infrastructure/Provider layers implement the adapter
    - No dependency on concrete implementations
    """

    @abstractmethod
    def resolve_with_fallback(self, parameter: str) -> str:
        """
        Resolve a template parameter with fallback to original value.

        Args:
            parameter: Parameter to resolve (e.g., SSM parameter path)

        Returns:
            Resolved value or original parameter if resolution fails
        """

    @abstractmethod
    def resolve_parameter(self, parameter: str) -> Optional[str]:
        """
        Resolve a template parameter.

        Args:
            parameter: Parameter to resolve

        Returns:
            Resolved value or None if resolution fails
        """

    @abstractmethod
    def is_resolvable(self, parameter: str) -> bool:
        """
        Check if a parameter can be resolved by this resolver.

        Args:
            parameter: Parameter to check

        Returns:
            True if parameter can be resolved
        """

    @abstractmethod
    def get_resolver_type(self) -> str:
        """
        Get the type of resolver (e.g., 'ami', 'ssm', 'parameter').

        Returns:
            String identifying the resolver type
        """
