"""Generic image resolution interface for template domain."""

from abc import ABC, abstractmethod


class ImageResolver(ABC):
    """
    Generic interface for resolving image references to actual image IDs.

    This interface allows the domain layer to resolve image references without
    depending on specific cloud provider implementations (AWS AMI, Provider1 Image, Provider2 Image, etc.).
    """

    @abstractmethod
    def resolve_image_id(self, image_reference: str) -> str:
        """
        Resolve image reference to actual image ID.

        Args:
            image_reference: Image reference (could be alias, parameter path, or direct ID)

        Returns:
            Resolved image ID

        Raises:
            ValueError: If image cannot be resolved
        """

    @abstractmethod
    def supports_reference_format(self, image_reference: str) -> bool:
        """
        Check if this resolver supports the given image reference format.

        Args:
            image_reference: Image reference to check

        Returns:
            True if this resolver can handle the reference format
        """
