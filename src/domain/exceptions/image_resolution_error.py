"""Image resolution domain exception."""

from src.domain.base.exceptions import DomainError


class ImageResolutionError(DomainError):
    """Raised when image specification cannot be resolved to actual image ID."""

    def __init__(self, message: str, image_specification: str = None):
        super().__init__(message)
        self.image_specification = image_specification
