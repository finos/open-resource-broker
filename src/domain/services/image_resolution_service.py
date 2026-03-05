"""Generic image resolution domain service interface."""

from abc import ABC, abstractmethod


class ImageResolutionService(ABC):
    """
    Domain service for resolving machine image identifiers.

    Resolves image specifications (parameters, aliases, IDs) to actual
    image identifiers that can be used for machine provisioning.

    Provider-agnostic: Works with AWS AMIs, Provider1 VM images, Provider2 images, etc.
    """

    @abstractmethod
    def resolve_image_id(self, image_specification: str) -> str:
        """
        Resolve image specification to actual image ID.

        Args:
            image_specification: Image specification (parameter, alias, or direct ID)

        Returns:
            Resolved image ID that can be used for provisioning

        Raises:
            ImageResolutionError: If image cannot be resolved
        """
        pass

    @abstractmethod
    def is_resolution_needed(self, image_specification: str) -> bool:
        """
        Check if image specification needs resolution.

        Args:
            image_specification: Image specification to check

        Returns:
            True if resolution is needed, False if already a direct image ID
        """
        pass
