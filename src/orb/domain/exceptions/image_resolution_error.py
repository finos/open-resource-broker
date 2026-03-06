"""Image resolution domain exception."""

from src.domain.base.exceptions import DomainException


class ImageResolutionError(DomainException):
    """Raised when image specification cannot be resolved to actual image ID."""

    def __init__(self, message: str, image_specification: str = None):  # type: ignore[assignment]
        super().__init__(message)
        self.image_specification = image_specification
